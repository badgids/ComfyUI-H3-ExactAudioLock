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
                "MiniMaxH3DialogueAudioLock",
                "MiniMaxH3SceneTimedAudio",
                "MiniMaxH3SceneExactAudioLock",
                "MiniMaxH3SceneDialogueAudioLock",
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
        video = torch.zeros((1, 24, 2, 2, 2))
        audio = torch.zeros((1, 32, 2, 4))
        upstream_video_mask = torch.zeros_like(video)
        upstream_audio_mask = torch.ones_like(audio)
        latent = {
            "samples": _FakeNestedTensor((video, audio)),
            "noise_mask": _FakeNestedTensor((upstream_video_mask, upstream_audio_mask)),
        }
        output = mod.MiniMaxH3ExactAudioLock.execute(
            latent, _AudioVAE(), timed_audios={}
        )
        locked, exact_audio, manifest = output.values

        video_mask, audio_mask = locked["noise_mask"].unbind()
        self.assertTrue(torch.equal(video_mask, upstream_video_mask))
        self.assertTrue(torch.equal(audio_mask, torch.zeros_like(audio_mask)))
        self.assertEqual(exact_audio["sample_rate"], 32000)
        self.assertIn('"track_count":0', manifest)


    def test_exact_lock_rejects_malformed_upstream_noise_mask(self):
        latent = {
            "samples": _FakeNestedTensor((
                torch.zeros((1, 24, 2, 2, 2)),
                torch.zeros((1, 32, 2, 4)),
            )),
            "noise_mask": _FakeNestedTensor((
                torch.ones((1, 24, 1, 2, 2)),
                torch.ones((1, 32, 2, 4)),
            )),
        }
        with self.assertRaisesRegex(ValueError, "video noise_mask shape"):
            mod.MiniMaxH3ExactAudioLock.execute(latent, _AudioVAE(), timed_audios={})

    def test_existing_noise_mask_values_must_be_normalized(self):
        video = torch.zeros((1, 24, 2, 2, 2))
        audio = torch.zeros((1, 32, 2, 4))
        video_mask = torch.ones_like(video)
        audio_mask = torch.ones_like(audio)
        audio_mask[..., 1] = 1.25
        latent = {
            "samples": _FakeNestedTensor((video, audio)),
            "noise_mask": _FakeNestedTensor((video_mask, audio_mask)),
        }
        with self.assertRaisesRegex(ValueError, "audio noise_mask values"):
            mod.MiniMaxH3DialogueAudioLock.execute(
                latent, _AudioVAE(),
                timed_audios={"timed_audio_0": {
                    "audio": {"waveform": torch.ones((1,1,800)), "sample_rate": 32000},
                    "start_frame": 0, "gain_db": 0.0, "label": "line",
                }},
            )

    def test_dialogue_mask_has_exact_core_and_generative_gaps(self):
        template = torch.zeros((1, 32, 2, 12))
        tracks = [{"start_sample": 1600, "end_sample": 3200, "mixed_samples": 1600}]
        mask, protection = mod._dialogue_noise_mask(
            template, tracks, sample_rate=32000,
            protect_before_ms=0, protect_after_ms=0, feather_ms=0,
        )
        temporal = mask[0, 0, 0]
        self.assertTrue(torch.equal(temporal[:2], torch.ones(2)))
        self.assertTrue(torch.equal(temporal[2:4], torch.zeros(2)))
        self.assertTrue(torch.equal(temporal[4:], torch.ones(8)))
        self.assertEqual(protection[0]["core_start_audio_frame"], 2)
        self.assertEqual(protection[0]["core_end_audio_frame"], 4)

    def test_dialogue_mask_feather_is_soft_but_core_stays_zero(self):
        template = torch.zeros((1, 32, 2, 12))
        tracks = [{"start_sample": 3200, "end_sample": 4000, "mixed_samples": 800}]
        mask, _ = mod._dialogue_noise_mask(
            template, tracks, sample_rate=32000,
            protect_before_ms=0, protect_after_ms=0, feather_ms=50,
        )
        temporal = mask[0, 0, 0]
        self.assertEqual(float(temporal[4]), 0.0)
        self.assertTrue(0.0 < float(temporal[3]) < 1.0)
        self.assertTrue(0.0 < float(temporal[5]) < 1.0)

    def test_dialogue_lock_preserves_video_mask_and_combines_audio_mask(self):
        video = torch.zeros((1, 24, 2, 2, 2))
        audio = torch.zeros((1, 32, 2, 4))
        video_mask = torch.zeros_like(video)
        audio_mask = torch.ones_like(audio)
        audio_mask[..., 3] = 0
        latent = {
            "samples": _FakeNestedTensor((video, audio)),
            "noise_mask": _FakeNestedTensor((video_mask, audio_mask)),
        }
        event_audio = {"waveform": torch.ones((1, 1, 800)), "sample_rate": 32000}
        event = {"audio": event_audio, "start_frame": 0, "gain_db": 0.0, "label": "line"}
        output = mod.MiniMaxH3DialogueAudioLock.execute(
            latent, _AudioVAE(), timed_audios={"timed_audio_0": event},
            protect_before_ms=0, protect_after_ms=0, feather_ms=0,
        )
        locked, reference, manifest_json = output.values
        out_video_mask, out_audio_mask = locked["noise_mask"].unbind()
        self.assertTrue(torch.equal(out_video_mask, video_mask))
        self.assertEqual(float(out_audio_mask[..., 0].max()), 0.0)
        self.assertEqual(float(out_audio_mask[..., 1].min()), 1.0)
        self.assertEqual(float(out_audio_mask[..., 3].max()), 0.0)
        self.assertEqual(reference["sample_rate"], 32000)
        manifest = __import__("json").loads(manifest_json)
        self.assertFalse(manifest["reference_audio_is_final_mix"])
        self.assertEqual(manifest["mode"], "dialogue_partial_lock")

    def test_dialogue_lock_preserves_unprotected_target_audio_samples(self):
        video = torch.zeros((1, 24, 2, 2, 2))
        audio = torch.full((1, 32, 2, 4), 7.0)
        latent = {"samples": _FakeNestedTensor((video, audio))}
        event_audio = {"waveform": torch.ones((1, 1, 800)), "sample_rate": 32000}
        event = {"audio": event_audio, "start_frame": 0, "gain_db": 0.0, "label": "line"}
        output = mod.MiniMaxH3DialogueAudioLock.execute(
            latent, _AudioVAE(), timed_audios={"timed_audio_0": event},
            protect_before_ms=0, protect_after_ms=0, feather_ms=0,
        )
        locked = output.values[0]
        _, partial_audio = locked["samples"].unbind()
        self.assertEqual(float(partial_audio[..., 0].max()), 0.0)
        self.assertEqual(float(partial_audio[..., 1].min()), 7.0)
        self.assertEqual(float(partial_audio[..., 3].min()), 7.0)

    def test_dialogue_lock_requires_an_event_and_valid_margins(self):
        latent = {"samples": _FakeNestedTensor((
            torch.zeros((1, 24, 2, 2, 2)),
            torch.zeros((1, 32, 2, 4)),
        ))}
        with self.assertRaisesRegex(ValueError, "at least one Timed Audio"):
            mod.MiniMaxH3DialogueAudioLock.execute(latent, _AudioVAE(), timed_audios={})
        with self.assertRaisesRegex(ValueError, "between 0 and 5000"):
            mod.MiniMaxH3DialogueAudioLock.execute(
                latent, _AudioVAE(), timed_audios={}, protect_before_ms=-1
            )

    def test_scene_dialogue_lock_selects_current_scene(self):
        scheduled = {
            "scene_timed_audio_0": {"audio": object(), "scene_index": 1, "start_frame": 0, "gain_db": 0.0, "label": "A"},
            "scene_timed_audio_1": {"audio": object(), "scene_index": 2, "start_frame": 8, "gain_db": 0.0, "label": "B"},
        }
        sentinel = _NodeOutput("latent", "reference", '{"mode":"dialogue_partial_lock"}')
        with mock.patch.object(mod.MiniMaxH3DialogueAudioLock, "execute", return_value=sentinel) as delegate:
            output = mod.MiniMaxH3SceneDialogueAudioLock.execute("latent", "vae", 2, scheduled)
        self.assertEqual(output.values[0], "latent")
        selected = delegate.call_args.kwargs["timed_audios"]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected["timed_audio_0"]["label"], "B")
        manifest = __import__("json").loads(output.values[2])
        self.assertEqual(manifest["scene_index"], 2)

    def test_scene_dialogue_empty_generate_is_true_passthrough_and_does_not_encode(self):
        latent = {"samples": _FakeNestedTensor((
            torch.zeros((1, 24, 2, 2, 2)),
            torch.zeros((1, 32, 2, 4)),
        ))}
        vae = _AudioVAE()
        vae.encode = mock.Mock(side_effect=AssertionError("encode must not run"))
        output = mod.MiniMaxH3SceneDialogueAudioLock.execute(
            latent, vae, 7, {}, empty_scene_policy="generate"
        )
        self.assertEqual(output.values[0], latent)
        vae.encode.assert_not_called()
        manifest = __import__("json").loads(output.values[2])
        self.assertEqual(manifest["track_count"], 0)
        self.assertEqual(manifest["empty_scene_policy"], "generate")

    def test_scene_dialogue_empty_error_is_explicit(self):
        with self.assertRaisesRegex(ValueError, "No Scene Timed Audio events"):
            mod.MiniMaxH3SceneDialogueAudioLock.execute(
                "latent", "vae", 2, {}, empty_scene_policy="error"
            )


if __name__ == "__main__":
    unittest.main()
