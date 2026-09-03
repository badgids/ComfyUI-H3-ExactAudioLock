from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

import torch


ROOT = Path(__file__).resolve().parents[1]


class _FakeNestedTensor:
    def __init__(self, tensors):
        self.tensors = tuple(tensors)
        self.is_nested = True

    def unbind(self):
        return self.tensors


class _NodeOutput:
    def __init__(self, *values):
        self.values = values


class _SocketType:
    @staticmethod
    def Input(*args, **kwargs):
        return object()

    @staticmethod
    def Output(*args, **kwargs):
        return object()


class _CustomType:
    def __init__(self, name):
        self.name = name

    def Input(self, *args, **kwargs):
        return object()

    def Output(self, *args, **kwargs):
        return object()


class _Autogrow:
    Type = object

    class TemplatePrefix:
        def __init__(self, *args, **kwargs):
            pass

    @staticmethod
    def Input(*args, **kwargs):
        return object()


class _Schema:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


class _ComfyNode:
    pass


class _ComfyExtension:
    pass


def _load_module():
    comfy = types.ModuleType("comfy")
    nested = types.ModuleType("comfy.nested_tensor")
    nested.NestedTensor = _FakeNestedTensor
    comfy.nested_tensor = nested
    sys.modules["comfy"] = comfy
    sys.modules["comfy.nested_tensor"] = nested

    api = types.ModuleType("comfy_api")
    latest = types.ModuleType("comfy_api.latest")
    io = types.SimpleNamespace(
        Custom=_CustomType,
        ComfyNode=_ComfyNode,
        NodeOutput=_NodeOutput,
        Schema=_Schema,
        Autogrow=_Autogrow,
        Audio=_SocketType,
        Int=_SocketType,
        Float=_SocketType,
        String=_SocketType,
        Latent=_SocketType,
        Vae=_SocketType,
        Combo=_SocketType,
    )
    latest.ComfyExtension = _ComfyExtension
    latest.io = io
    api.latest = latest
    sys.modules["comfy_api"] = api
    sys.modules["comfy_api.latest"] = latest

    spec = importlib.util.spec_from_file_location(
        "exact_audio_lock_under_test",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


mod = _load_module()


class _AudioVAE:
    audio_sample_rate = 32000

    def encode(self, waveform):
        # Input is [B,T,C]; return MiniMax H3 [B,32,2,T40].
        return torch.zeros((1, 32, 2, 4), dtype=torch.float32)


class ExactAudioLockTests(unittest.TestCase):
    def test_existing_and_scene_nodes_are_registered(self):
        nodes = asyncio.run(mod.H3ExactAudioLockExtension().get_node_list())
        self.assertEqual(
            [node.__name__ for node in nodes],
            [
                "MiniMaxH3TimedAudio",
                "MiniMaxH3ExactAudioLock",
                "MiniMaxH3SceneTimedAudio",
                "MiniMaxH3SceneExactAudioLock",
            ],
        )

    def test_scene_timed_audio_requires_one_based_scene(self):
        audio = {"waveform": torch.zeros((1, 1, 8)), "sample_rate": 32000}
        output = mod.MiniMaxH3SceneTimedAudio.execute(audio, 2, 24, 0.0, "speaker")
        event = output.values[0]
        self.assertEqual(event["scene_index"], 2)
        self.assertEqual(event["start_frame"], 24)
        self.assertIs(event["audio"], audio)

        with self.assertRaisesRegex(ValueError, "at least 1"):
            mod.MiniMaxH3SceneTimedAudio.execute(audio, 0, 0, 0.0, "")

        with self.assertRaisesRegex(ValueError, "one-based integer"):
            mod.MiniMaxH3SceneTimedAudio.execute(audio, True, 0, 0.0, "")

    def test_scene_lock_selects_only_current_scene_and_delegates(self):
        audio_a = object()
        audio_b = object()
        scheduled = {
            "scene_timed_audio_0": {
                "audio": audio_a,
                "scene_index": 1,
                "start_frame": 12,
                "gain_db": 0.0,
                "label": "A",
            },
            "scene_timed_audio_1": {
                "audio": audio_b,
                "scene_index": 2,
                "start_frame": 24,
                "gain_db": -1.0,
                "label": "B",
            },
        }
        sentinel = _NodeOutput("locked", "audio", "manifest")

        with mock.patch.object(
            mod.MiniMaxH3ExactAudioLock, "execute", return_value=sentinel
        ) as delegate:
            result = mod.MiniMaxH3SceneExactAudioLock.execute(
                "latent", "vae", 2, scheduled
            )

        self.assertIs(result, sentinel)
        kwargs = delegate.call_args.kwargs
        self.assertEqual(list(kwargs["timed_audios"]), ["timed_audio_0"])
        selected = kwargs["timed_audios"]["timed_audio_0"]
        self.assertIs(selected["audio"], audio_b)
        self.assertNotIn("scene_index", selected)
        self.assertEqual(selected["start_frame"], 24)

    def test_scene_lock_accepts_flattened_autogrow_runtime_form(self):
        event = {
            "audio": object(),
            "scene_index": 3,
            "start_frame": 6,
            "gain_db": 0.0,
            "label": "line",
        }
        sentinel = _NodeOutput("locked", "audio", "manifest")
        runtime = {"scene_timed_audios.scene_timed_audio_0": event}

        with mock.patch.object(
            mod.MiniMaxH3ExactAudioLock, "execute", return_value=sentinel
        ) as delegate:
            result = mod.MiniMaxH3SceneExactAudioLock.execute(
                "latent", "vae", 3, None, **runtime
            )

        self.assertIs(result, sentinel)
        self.assertEqual(
            delegate.call_args.kwargs["timed_audios"]["timed_audio_0"]["start_frame"],
            6,
        )

    def test_scene_lock_rejects_unexpected_flattened_runtime_input(self):
        with self.assertRaisesRegex(
            TypeError, "MiniMaxH3SceneExactAudioLock received unexpected runtime input"
        ):
            mod.MiniMaxH3SceneExactAudioLock.execute(
                "latent",
                "vae",
                1,
                None,
                **{"wrong_group.scene_timed_audio_0": {}},
            )

    def test_scene_lock_empty_scene_is_explicit(self):
        with self.assertRaisesRegex(ValueError, "No Scene Timed Audio events"):
            mod.MiniMaxH3SceneExactAudioLock.execute(
                "latent", "vae", 4, {}, empty_scene_policy="error"
            )

        sentinel = _NodeOutput("locked", "audio", "manifest")
        with mock.patch.object(
            mod.MiniMaxH3ExactAudioLock, "execute", return_value=sentinel
        ) as delegate:
            result = mod.MiniMaxH3SceneExactAudioLock.execute(
                "latent", "vae", 4, {}, empty_scene_policy="lock_silence"
            )
        self.assertIs(result, sentinel)
        self.assertEqual(delegate.call_args.kwargs["timed_audios"], {})

    def test_scene_lock_validates_every_connected_event(self):
        scheduled = {
            "scene_timed_audio_0": {
                "audio": object(),
                "scene_index": 0,
                "start_frame": 0,
                "gain_db": 0.0,
                "label": "bad",
            }
        }
        with self.assertRaisesRegex(ValueError, "at least 1"):
            mod.MiniMaxH3SceneExactAudioLock.execute(
                "latent", "vae", 99, scheduled
            )

    def test_audio_batch_is_not_silently_truncated(self):
        with self.assertRaisesRegex(ValueError, "exactly one waveform batch"):
            mod._to_stereo(torch.zeros((2, 1, 8)))

    def test_nonfinite_audio_is_rejected_before_vae_encode(self):
        waveform = torch.zeros((1, 1, 8))
        waveform[..., 3] = float("nan")
        entry = {
            "audio": {"waveform": waveform, "sample_rate": 32000},
            "start_frame": 0,
            "gain_db": 0.0,
            "label": "bad",
        }
        with self.assertRaisesRegex(ValueError, "NaN or infinite"):
            mod._prepare_track(entry, 32000)

    def test_invalid_target_timeline_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "at least one waveform sample"):
            mod._mix_tracks(
                [], target_samples=0, sample_rate=32000,
                mix_policy="sum", overflow_policy="error"
            )

    def test_exact_lock_validates_h3_stream_shapes(self):
        bad = {
            "samples": _FakeNestedTensor(
                (
                    torch.zeros((1, 23, 2, 2, 2)),
                    torch.zeros((1, 32, 2, 4)),
                )
            )
        }
        with self.assertRaisesRegex(ValueError, "video latent shape"):
            mod.MiniMaxH3ExactAudioLock.execute(bad, _AudioVAE(), timed_audios={})

    def test_exact_lock_happy_path_locks_audio_and_leaves_video_denoisable(self):
        latent = {
            "samples": _FakeNestedTensor(
                (
                    torch.zeros((1, 24, 2, 2, 2)),
                    torch.zeros((1, 32, 2, 4)),
                )
            )
        }
        output = mod.MiniMaxH3ExactAudioLock.execute(
            latent, _AudioVAE(), timed_audios={}
        )
        locked, exact_audio, manifest = output.values

        video_mask, audio_mask = locked["noise_mask"].unbind()
        self.assertTrue(torch.equal(video_mask, torch.ones_like(video_mask)))
        self.assertTrue(torch.equal(audio_mask, torch.zeros_like(audio_mask)))
        self.assertEqual(exact_audio["sample_rate"], 32000)
        self.assertIn('"track_count":0', manifest)


if __name__ == "__main__":
    unittest.main()
