from __future__ import annotations

import asyncio
import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]


class _NodeOutput:
    def __init__(self, *values): self.values = values


class _Socket:
    @staticmethod
    def Input(*args, **kwargs): return {"args": args, "kwargs": kwargs}
    @staticmethod
    def Output(*args, **kwargs): return {"args": args, "kwargs": kwargs}


class _TemplatePrefix:
    def __init__(self, input=None, prefix="", min=0, max=None, **kwargs):
        self.input, self.prefix, self.min, self.max = input, prefix, min, max


class _Autogrow:
    Type = dict
    TemplatePrefix = _TemplatePrefix
    @staticmethod
    def Input(*args, **kwargs): return {"args": args, "kwargs": kwargs}


class _ComfyNode: pass


def load_module(temp_root: str):
    latest = types.ModuleType("comfy_api.latest")
    latest.io = types.SimpleNamespace(
        ComfyNode=_ComfyNode, NodeOutput=_NodeOutput,
        Schema=lambda *a, **k: (a, k), Audio=_Socket, String=_Socket,
        Boolean=_Socket, Int=_Socket, Combo=_Socket, Autogrow=_Autogrow,
        UploadType=types.SimpleNamespace(audio="audio"),
        Hidden=types.SimpleNamespace(unique_id="unique_id"),
    )
    comfy_api = types.ModuleType("comfy_api")
    comfy_api.latest = latest
    sys.modules["comfy_api"] = comfy_api
    sys.modules["comfy_api.latest"] = latest

    input_root = Path(temp_root) / "input"
    output_root = Path(temp_root) / "output"
    input_root.mkdir(parents=True, exist_ok=True)
    output_root.mkdir(parents=True, exist_ok=True)
    folder_paths = types.ModuleType("folder_paths")
    folder_paths.get_temp_directory = lambda: str(Path(temp_root) / "temp")
    folder_paths.get_input_directory = lambda: str(input_root)
    folder_paths.get_output_directory = lambda: str(output_root)
    folder_paths.get_annotated_filepath = lambda value: str(input_root / str(value))
    folder_paths.exists_annotated_filepath = lambda value: (input_root / str(value)).is_file()
    sys.modules["folder_paths"] = folder_paths

    spec = importlib.util.spec_from_file_location("audio_review_gate_under_test", ROOT / "audio_review_gate.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader
    spec.loader.exec_module(module)
    return module


class _FakeFrame:
    def __init__(self, array): self._array = np.asarray(array)
    def to_ndarray(self): return self._array


class _FakeContainer:
    def __init__(self, frames, sample_rate=44100, channels=2):
        stream = types.SimpleNamespace(codec_context=types.SimpleNamespace(sample_rate=sample_rate), channels=channels, index=0)
        self.streams = types.SimpleNamespace(audio=[stream])
        self._frames = [_FakeFrame(frame) for frame in frames]
    def __enter__(self): return self
    def __exit__(self, exc_type, exc, tb): return False
    def decode(self, streams=0): return iter(self._frames)


class AudioReviewGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.mod = load_module(self.temp.name)
        self.input_root = Path(self.temp.name) / "input"
        self.output_root = Path(self.temp.name) / "output"
        self.audio_a = {"waveform": torch.zeros((1, 2, 64)), "sample_rate": 32000}
        self.audio_b = {"waveform": torch.ones((1, 2, 64)), "sample_rate": 32000}

    def tearDown(self): self.temp.cleanup()

    def fake_save(self, filename, tensor, sample_rate, **kwargs):
        Path(filename).parent.mkdir(parents=True, exist_ok=True)
        Path(filename).write_bytes(b"RIFFfake")

    def test_validation_and_requested_extensions(self):
        waveform, rate = self.mod._validate_review_audio(self.audio_a)
        self.assertEqual(tuple(waveform.shape), (1, 2, 64))
        self.assertEqual(rate, 32000)
        for ext in (".wav", ".mp3", ".flac", ".ogg", ".oga", ".opus"):
            self.assertIn(ext, self.mod._SUPPORTED_AUDIO_EXTENSIONS)
        with self.assertRaisesRegex(ValueError, "NaN or infinite"):
            self.mod._validate_review_audio({"waveform": torch.tensor([[[float('nan')]]]), "sample_rate": 32000})

    def test_file_input_confined_and_unsupported_rejected(self):
        (Path(self.temp.name) / "outside.mp3").write_bytes(b"x")
        with self.assertRaisesRegex(ValueError, "input directory"):
            self.mod._resolve_input_audio_file("../outside.mp3")
        (self.input_root / "x.m4a").write_bytes(b"x")
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            self.mod._resolve_input_audio_file("x.m4a")

    def test_decoder_accepts_wav_mp3_flac_ogg(self):
        frames = [np.zeros((2, 4), np.float32), np.ones((2, 2), np.float32)]
        fake_av = types.ModuleType("av")
        fake_av.open = lambda _path: _FakeContainer(frames, sample_rate=48000, channels=2)
        with mock.patch.dict(sys.modules, {"av": fake_av}):
            for name in ("a.wav", "b.mp3", "c.flac", "d.ogg"):
                (self.input_root / name).write_bytes(b"encoded")
                audio, _path = self.mod._load_input_audio_file(name)
                self.assertEqual(audio["sample_rate"], 48000)
                self.assertEqual(tuple(audio["waveform"].shape), (1, 2, 6))

    def test_connected_candidates_accept_autogrow_and_split_batches(self):
        batched = {"waveform": torch.arange(24, dtype=torch.float32).reshape(2, 2, 6), "sample_rate": 24000}
        rows = self.mod._collect_review_candidates(
            "connected_audio", audio=self.audio_a,
            audio_candidates={"audio_candidate_0": self.audio_b, "audio_candidate_1": batched},
        )
        self.assertEqual(len(rows), 4)
        self.assertIs(rows[0]["audio"], self.audio_a)
        self.assertIs(rows[1]["audio"], self.audio_b)
        self.assertEqual(tuple(rows[2]["audio"]["waveform"].shape), (1, 2, 6))
        self.assertEqual([r["id"] for r in rows], ["candidate_001", "candidate_002", "candidate_003", "candidate_004"])

    def test_flattened_autogrow_candidates_are_supported(self):
        rows = self.mod._collect_review_candidates(
            "connected_audio", runtime_inputs={
                "audio_candidates.audio_candidate_0": self.audio_a,
                "audio_candidates.audio_candidate_1": self.audio_b,
            }
        )
        self.assertEqual(len(rows), 2)

    def test_file_candidates_reject_duplicates(self):
        path = self.input_root / "same.flac"
        path.write_bytes(b"x")
        with mock.patch.object(self.mod, "_load_input_audio_file", return_value=(self.audio_a, path.resolve())):
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                self.mod._collect_review_candidates("audio_file", audio_file="same.flac", audio_files={"audio_file_candidate_0": "same.flac"})

    def test_candidate_preview_is_one_player_per_candidate(self):
        rows = self.mod._collect_review_candidates("connected_audio", audio=self.audio_a, audio_candidates={"audio_candidate_0": self.audio_b})
        with mock.patch.object(self.mod.torchaudio, "save", side_effect=self.fake_save):
            previews, paths, review_dir, temp_root = self.mod._create_candidate_previews(rows, "tok")
        self.assertEqual(len(previews), 2)
        self.assertEqual(previews[0]["candidate_id"], "candidate_001")
        self.assertEqual(previews[1]["candidate_id"], "candidate_002")
        self.mod._cleanup_owned_review_files(paths, review_dir, temp_root)

    def test_accept_selected_returns_only_selected_candidate(self):
        rows = self.mod._collect_review_candidates("connected_audio", audio=self.audio_a, audio_candidates={"audio_candidate_0": self.audio_b})
        def accept_second(payload):
            ok, reason = self.mod._submit_audio_review_decision(payload["token"], "accept", "candidate_002", [])
            self.assertTrue(ok, reason)
        with mock.patch.object(self.mod.torchaudio, "save", side_effect=self.fake_save):
            accepted = self.mod._run_audio_candidate_review(rows, review_label="", delete_rejected_audio_files=True, review_timeout_seconds=1, notifier=accept_second)
        self.assertIs(accepted, self.audio_b)
        self.assertEqual(self.mod._list_pending_audio_reviews(), [])

    def test_accept_does_not_bundle_previous_or_other_candidates(self):
        for chosen, expected in (("candidate_001", self.audio_a), ("candidate_002", self.audio_b)):
            rows = self.mod._collect_review_candidates("connected_audio", audio=self.audio_a, audio_candidates={"audio_candidate_0": self.audio_b})
            def decide(payload, chosen=chosen):
                self.mod._submit_audio_review_decision(payload["token"], "accept", chosen, [])
            with mock.patch.object(self.mod.torchaudio, "save", side_effect=self.fake_save):
                accepted = self.mod._run_audio_candidate_review(rows, review_label="", delete_rejected_audio_files=True, review_timeout_seconds=1, notifier=decide)
            self.assertIs(accepted, expected)

    def test_marked_connected_alternate_is_saved_but_not_output(self):
        rows = self.mod._collect_review_candidates("connected_audio", audio=self.audio_a, audio_candidates={"audio_candidate_0": self.audio_b})
        def decide(payload):
            self.mod._submit_audio_review_decision(payload["token"], "accept", "candidate_001", ["candidate_002"])
        with mock.patch.object(self.mod.torchaudio, "save", side_effect=self.fake_save):
            accepted = self.mod._run_audio_candidate_review(rows, review_label="", delete_rejected_audio_files=True, review_timeout_seconds=1, notifier=decide)
        self.assertIs(accepted, self.audio_a)
        alternates = list((self.output_root / "h3_exact_audio_lock_review" / "alternates").rglob("candidate_002.wav"))
        self.assertEqual(len(alternates), 1)

    def test_marked_file_alternate_is_copied_preserving_format(self):
        a = self.input_root / "a.mp3"; b = self.input_root / "b.flac"
        a.write_bytes(b"aaa"); b.write_bytes(b"bbb")
        candidates = [
            {"id":"candidate_001","number":1,"audio":self.audio_a,"label":"a.mp3","source_path":a.resolve(),"source_name":"a.mp3"},
            {"id":"candidate_002","number":2,"audio":self.audio_b,"label":"b.flac","source_path":b.resolve(),"source_name":"b.flac"},
        ]
        def decide(payload): self.mod._submit_audio_review_decision(payload["token"], "accept", "candidate_001", ["candidate_002"])
        with mock.patch.object(self.mod.torchaudio, "save", side_effect=self.fake_save):
            self.mod._run_audio_candidate_review(candidates, review_label="", delete_rejected_audio_files=True, review_timeout_seconds=1, notifier=decide)
        copies = list((self.output_root / "h3_exact_audio_lock_review" / "alternates").rglob("candidate_002_b.flac"))
        self.assertEqual(len(copies), 1)
        self.assertEqual(copies[0].read_bytes(), b"bbb")
        self.assertTrue(a.exists()); self.assertTrue(b.exists())

    def test_unselected_unkept_file_candidates_are_deleted_when_enabled(self):
        a = self.input_root / "a.mp3"; b = self.input_root / "b.flac"; c = self.input_root / "c.ogg"
        for p in (a,b,c): p.write_bytes(b"x")
        candidates = [
            {"id":"candidate_001","number":1,"audio":self.audio_a,"label":"a","source_path":a.resolve(),"source_name":a.name},
            {"id":"candidate_002","number":2,"audio":self.audio_b,"label":"b","source_path":b.resolve(),"source_name":b.name},
            {"id":"candidate_003","number":3,"audio":self.audio_b,"label":"c","source_path":c.resolve(),"source_name":c.name},
        ]
        def decide(payload): self.mod._submit_audio_review_decision(payload["token"], "accept", "candidate_001", ["candidate_002"])
        with mock.patch.object(self.mod.torchaudio, "save", side_effect=self.fake_save):
            self.mod._run_audio_candidate_review(candidates, review_label="", delete_rejected_audio_files=True, review_timeout_seconds=1, notifier=decide)
        self.assertTrue(a.exists()); self.assertTrue(b.exists()); self.assertFalse(c.exists())

    def test_delete_disabled_retains_unselected_files(self):
        a = self.input_root / "a.mp3"; b = self.input_root / "b.flac"
        a.write_bytes(b"a"); b.write_bytes(b"b")
        candidates = [
            {"id":"candidate_001","number":1,"audio":self.audio_a,"label":"a","source_path":a.resolve(),"source_name":a.name},
            {"id":"candidate_002","number":2,"audio":self.audio_b,"label":"b","source_path":b.resolve(),"source_name":b.name},
        ]
        def decide(payload): self.mod._submit_audio_review_decision(payload["token"], "accept", "candidate_001", [])
        with mock.patch.object(self.mod.torchaudio, "save", side_effect=self.fake_save):
            self.mod._run_audio_candidate_review(candidates, review_label="", delete_rejected_audio_files=False, review_timeout_seconds=1, notifier=decide)
        self.assertTrue(a.exists()); self.assertTrue(b.exists())

    def test_reject_all_can_still_save_marked_alternate(self):
        rows = self.mod._collect_review_candidates("connected_audio", audio=self.audio_a, audio_candidates={"audio_candidate_0": self.audio_b})
        def decide(payload): self.mod._submit_audio_review_decision(payload["token"], "reject", None, ["candidate_002"])
        with mock.patch.object(self.mod.torchaudio, "save", side_effect=self.fake_save):
            with self.assertRaisesRegex(RuntimeError, "rejected"):
                self.mod._run_audio_candidate_review(rows, review_label="", delete_rejected_audio_files=True, review_timeout_seconds=1, notifier=decide)
        self.assertEqual(len(list((self.output_root / "h3_exact_audio_lock_review" / "alternates").rglob("candidate_002.wav"))), 1)

    def test_invalid_selected_or_kept_candidate_fails_closed(self):
        rows = self.mod._collect_review_candidates("connected_audio", audio=self.audio_a)
        event = self.mod.threading.Event()
        record = {"token":"t","action":None,"event":event,"candidates":rows,"label":"","created_at":0,"deadline":1,"delete_rejected_audio_files":True,"previews":[]}
        with self.mod._PENDING_LOCK: self.mod._PENDING_REVIEWS["t"] = record
        self.assertEqual(self.mod._submit_audio_review_decision("t", "accept", "missing", []), (False, "invalid_selected_candidate"))
        self.assertEqual(self.mod._submit_audio_review_decision("t", "accept", "candidate_001", ["missing"]), (False, "invalid_kept_candidates"))

    def test_timeout_cleans_previews_and_never_deletes_source_files(self):
        source = self.input_root / "timeout.flac"; source.write_bytes(b"keep")
        candidates = [{"id":"candidate_001","number":1,"audio":self.audio_a,"label":"x","source_path":source.resolve(),"source_name":source.name}]
        with mock.patch.object(self.mod.torchaudio, "save", side_effect=self.fake_save):
            with self.assertRaisesRegex(TimeoutError, "timed out"):
                self.mod._run_audio_candidate_review(candidates, review_label="", delete_rejected_audio_files=True, review_timeout_seconds=.01, notifier=lambda _p: None)
        self.assertTrue(source.exists())

    def test_notification_failure_cleans_pending_state(self):
        rows = self.mod._collect_review_candidates("connected_audio", audio=self.audio_a)
        with mock.patch.object(self.mod.torchaudio, "save", side_effect=self.fake_save):
            with self.assertRaisesRegex(RuntimeError, "notification failed"):
                self.mod._run_audio_candidate_review(rows, review_label="", delete_rejected_audio_files=True, review_timeout_seconds=1, notifier=lambda _p: (_ for _ in ()).throw(RuntimeError("boom")))
        self.assertEqual(self.mod._list_pending_audio_reviews(), [])

    def test_public_payload_contains_candidate_metadata_not_private_audio_or_paths(self):
        rows = self.mod._collect_review_candidates("connected_audio", audio=self.audio_a)
        record = {"token":"t","label":"x","created_at":1,"deadline":2,"delete_rejected_audio_files":True,"previews":[{"candidate_id":"candidate_001"}],"candidates":rows,"paths":[Path('/private')],"event":self.mod.threading.Event(),"action":None}
        public = self.mod._public_review(record)
        self.assertIn("candidates", public)
        self.assertEqual(public["node_id"], "")
        self.assertNotIn("paths", public)
        self.assertNotIn("event", public)
        self.assertNotIn("waveform", str(public))
        self.assertNotIn("source_path", str(public))

    def test_file_fingerprint_covers_all_candidates(self):
        (self.input_root / "a.mp3").write_bytes(b"a")
        (self.input_root / "b.ogg").write_bytes(b"b")
        first = self.mod.AudioReviewAcceptGate.fingerprint_inputs(source_mode="audio_file", audio_file="a.mp3", audio_files={"audio_file_candidate_0":"b.ogg"})
        (self.input_root / "b.ogg").write_bytes(b"changed")
        second = self.mod.AudioReviewAcceptGate.fingerprint_inputs(source_mode="audio_file", audio_file="a.mp3", audio_files={"audio_file_candidate_0":"b.ogg"})
        self.assertNotEqual(first, second)
        self.assertIsNone(self.mod.AudioReviewAcceptGate.fingerprint_inputs(source_mode="connected_audio"))

    def test_schema_has_autogrow_audio_and_file_candidates(self):
        _args, kwargs = self.mod.AudioReviewAcceptGate.define_schema()
        self.assertEqual(kwargs["node_id"], "H3ExactAudioLockAudioReviewGate")
        ids = [item["args"][0] for item in kwargs["inputs"]]
        self.assertIn("audio_candidates", ids)
        self.assertIn("audio_files", ids)
        self.assertEqual(kwargs["outputs"][0]["kwargs"]["display_name"], "accepted_audio")
        self.assertEqual(kwargs["hidden"], ["unique_id"])

    def test_execute_returns_only_review_selected_audio(self):
        with mock.patch.object(self.mod, "_collect_review_candidates", return_value=[{"id":"candidate_001","number":1,"audio":self.audio_b,"label":"x","source_path":None,"source_name":""}]), mock.patch.object(self.mod, "_run_audio_candidate_review", return_value=self.audio_b):
            out = self.mod.AudioReviewAcceptGate.execute(source_mode="connected_audio", audio=self.audio_a)
        self.assertIs(out.values[0], self.audio_b)

    def test_alternate_save_failure_cleans_review_previews_and_keeps_sources(self):
        source = self.input_root / "alt.flac"
        source.write_bytes(b"keep")
        candidates = [
            {"id":"candidate_001","number":1,"audio":self.audio_a,"label":"main","source_path":None,"source_name":""},
            {"id":"candidate_002","number":2,"audio":self.audio_b,"label":"alt","source_path":source.resolve(),"source_name":source.name},
        ]
        def decide(payload):
            self.mod._submit_audio_review_decision(payload["token"], "accept", "candidate_001", ["candidate_002"])
        with mock.patch.object(self.mod.torchaudio, "save", side_effect=self.fake_save), \
             mock.patch.object(self.mod.shutil, "copy2", side_effect=OSError("copy failed")):
            with self.assertRaisesRegex(OSError, "copy failed"):
                self.mod._run_audio_candidate_review(
                    candidates, review_label="", delete_rejected_audio_files=True,
                    review_timeout_seconds=1, notifier=decide
                )
        self.assertTrue(source.exists())
        review_root = Path(self.temp.name) / "temp" / self.mod._REVIEW_SUBDIR
        self.assertFalse(review_root.exists() and any(review_root.rglob("*.wav")))

    def test_selected_candidate_is_not_duplicated_as_saved_alternate(self):
        rows = self.mod._collect_review_candidates("connected_audio", audio=self.audio_a)
        def decide(payload):
            self.mod._submit_audio_review_decision(payload["token"], "accept", "candidate_001", ["candidate_001"])
        with mock.patch.object(self.mod.torchaudio, "save", side_effect=self.fake_save):
            accepted = self.mod._run_audio_candidate_review(
                rows, review_label="", delete_rejected_audio_files=True,
                review_timeout_seconds=1, notifier=decide
            )
        self.assertIs(accepted, self.audio_a)
        alternate_root = self.output_root / "h3_exact_audio_lock_review" / "alternates"
        self.assertFalse(alternate_root.exists() and any(alternate_root.rglob("*")))

    def test_http_route_rejects_bad_candidate_selection(self):
        class FakeRoutes:
            def __init__(self): self.handlers = {}; self.registrations = 0
            def _d(self, method, path):
                def deco(fn): self.registrations += 1; self.handlers[(method,path)] = fn; return fn
                return deco
            def get(self,p): return self._d("GET",p)
            def post(self,p): return self._d("POST",p)
        routes=FakeRoutes(); server=types.ModuleType("server")
        server.PromptServer=types.SimpleNamespace(instance=types.SimpleNamespace(routes=routes))
        sys.modules["server"]=server
        self.mod._ROUTES_REGISTERED=False; self.mod.register_audio_review_routes(); self.mod.register_audio_review_routes()
        self.assertEqual(routes.registrations, 2)
        class Req:
            def __init__(self,b): self.b=b
            async def json(self): return self.b
        decision=routes.handlers[("POST",self.mod._DECISION_ROUTE)]
        response=asyncio.run(decision(Req({"token":"missing","action":"accept","selected_candidate_id":"candidate_001","kept_candidate_ids":[]})))
        self.assertEqual(response.status,404)

    def test_sequential_reviews_leave_no_ui_backend_history(self):
        for audio in (self.audio_a, self.audio_b):
            rows = self.mod._collect_review_candidates("connected_audio", audio=audio)
            def decide(payload): self.mod._submit_audio_review_decision(payload["token"], "accept", "candidate_001", [])
            with mock.patch.object(self.mod.torchaudio, "save", side_effect=self.fake_save):
                accepted = self.mod._run_audio_candidate_review(rows, review_label="", delete_rejected_audio_files=True, review_timeout_seconds=1, notifier=decide)
            self.assertIs(accepted, audio)
            self.assertEqual(self.mod._list_pending_audio_reviews(), [])


if __name__ == "__main__": unittest.main()
