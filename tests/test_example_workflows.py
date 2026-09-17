import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WF_ROOT = ROOT / "example_workflows"

NO_TITLE_TYPES = {
    "H3ExactAudioLockAudioReviewGate",
    "MiniMaxH3TimedAudio",
    "MiniMaxH3ExactAudioLock",
    "MiniMaxH3DialogueAudioLock",
    "MiniMaxH3DialogueAudioFinalize",
    "MiniMaxH3SceneTimedAudio",
    "MiniMaxH3SceneExactAudioLock",
    "MiniMaxH3SceneDialogueAudioLock",
    "MiniMaxH3AddGuide",
    "FB_Qwen3TTSVoiceDesign",
    "QwenTTSModelDownloader",
    "QwenTTSVoiceDesignNode",
    "Qwen3Loader",
    "Qwen3CustomVoice",
}

REQUIRED_TYPES = {
    "H3ExactAudioLockAudioReviewGate",
    "MiniMaxH3TimedAudio",
    "MiniMaxH3ExactAudioLock",
    "MiniMaxH3DialogueAudioLock",
    "MiniMaxH3DialogueAudioFinalize",
    "MiniMaxH3SceneTimedAudio",
    "MiniMaxH3SceneExactAudioLock",
    "MiniMaxH3SceneDialogueAudioLock",
    "LoadAudio",
    "MiniMaxH3ImageToVideo",
    "MiniMaxH3ReferenceToVideo",
    "MiniMaxH3AddGuide",
    "FB_Qwen3TTSVoiceDesign",
    "QwenTTSModelDownloader",
    "QwenTTSVoiceDesignNode",
    "Qwen3Loader",
    "Qwen3CustomVoice",
    "MiniMaxH3ChainCurrent",
    "MiniMaxH3ChainContext",
}

FULL_LOCK_TYPES = {"MiniMaxH3ExactAudioLock", "MiniMaxH3SceneExactAudioLock"}
PARTIAL_LOCK_TYPES = {"MiniMaxH3DialogueAudioLock", "MiniMaxH3SceneDialogueAudioLock"}


class ExampleWorkflowTests(unittest.TestCase):
    def workflows(self):
        files = sorted(WF_ROOT.rglob("*.json"))
        self.assertTrue(files, "no example workflows found")
        return files

    @staticmethod
    def load(path):
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def by_id(wf):
        return {node["id"]: node for node in wf["nodes"]}

    @staticmethod
    def incoming_link(wf, node, input_name):
        index = next(i for i, item in enumerate(node.get("inputs", [])) if item["name"] == input_name)
        link_id = node["inputs"][index].get("link")
        if link_id is None:
            return None
        return next(row for row in wf["links"] if row[0] == link_id)

    def test_all_json_loads_and_links_are_consistent(self):
        for path in self.workflows():
            with self.subTest(path=path.relative_to(ROOT)):
                wf = self.load(path)
                nodes = wf["nodes"]
                links = wf["links"]
                by_id = self.by_id(wf)
                self.assertEqual(len(by_id), len(nodes), "duplicate node id")
                by_link = {row[0]: row for row in links}
                self.assertEqual(len(by_link), len(links), "duplicate link id")
                for lid, src, src_slot, dst, dst_slot, typ in links:
                    self.assertIn(src, by_id)
                    self.assertIn(dst, by_id)
                    self.assertLess(src_slot, len(by_id[src].get("outputs", [])))
                    self.assertLess(dst_slot, len(by_id[dst].get("inputs", [])))
                    self.assertEqual(by_id[dst]["inputs"][dst_slot].get("link"), lid)
                    self.assertIn(lid, by_id[src]["outputs"][src_slot].get("links") or [])
                self.assertGreaterEqual(wf["last_node_id"], max(by_id))
                if links:
                    self.assertGreaterEqual(wf["last_link_id"], max(by_link))

    def test_no_nodes_overlap_or_hide_each_other(self):
        margin = 40
        for path in self.workflows():
            wf = self.load(path)
            nodes = wf["nodes"]
            for index, left in enumerate(nodes):
                lx, ly = map(float, left["pos"])
                lw, lh = map(float, left["size"])
                for right in nodes[index + 1 :]:
                    rx, ry = map(float, right["pos"])
                    rw, rh = map(float, right["size"])
                    separated = (
                        lx + lw + margin <= rx
                        or rx + rw + margin <= lx
                        or ly + lh + margin <= ry
                        or ry + rh + margin <= ly
                    )
                    self.assertTrue(
                        separated,
                        f"{path.name}: nodes {left['id']} ({left['type']}) and "
                        f"{right['id']} ({right['type']}) overlap or are closer than {margin}px",
                    )

    def test_real_node_labels_are_not_overridden(self):
        for path in self.workflows():
            wf = self.load(path)
            for node in wf["nodes"]:
                if node["type"] in NO_TITLE_TYPES:
                    self.assertNotIn(
                        "title",
                        node,
                        f"{path.name}: {node['type']} must use its upstream display label",
                    )

    def test_coverage_matrix(self):
        present = set()
        for path in self.workflows():
            wf = self.load(path)
            present.update(node["type"] for node in wf["nodes"])
        missing = REQUIRED_TYPES - present
        self.assertFalse(missing, f"missing required workflow node coverage: {sorted(missing)}")

    def test_native_add_guide_uses_real_current_input_order(self):
        expected = ["positive", "vae", "audio_vae", "latent", "image", "audio", "frame_idx"]
        for path in self.workflows():
            wf = self.load(path)
            for node in wf["nodes"]:
                if node["type"] == "MiniMaxH3AddGuide":
                    self.assertEqual([item["name"] for item in node["inputs"]], expected, path.name)

    def test_timed_audio_start_frame_drives_same_event_add_guide(self):
        for path in self.workflows():
            if "context_loop" in path.parts or "legacy_single_audio" in path.name:
                continue
            wf = self.load(path)
            by_id = self.by_id(wf)
            timed_nodes = [n for n in wf["nodes"] if n["type"] == "MiniMaxH3TimedAudio"]
            for timed in timed_nodes:
                self.assertGreaterEqual(len(timed["outputs"]), 2, f"{path.name}: Timed Audio lacks start_frame output")
                audio_link = self.incoming_link(wf, timed, "audio")
                self.assertIsNotNone(audio_link)
                audio_src = (audio_link[1], audio_link[2])
                matching = []
                for guide in (n for n in wf["nodes"] if n["type"] == "MiniMaxH3AddGuide"):
                    guide_audio = self.incoming_link(wf, guide, "audio")
                    guide_frame = self.incoming_link(wf, guide, "frame_idx")
                    if guide_audio and (guide_audio[1], guide_audio[2]) == audio_src:
                        matching.append((guide, guide_frame))
                self.assertEqual(len(matching), 1, f"{path.name}: expected one same-AUDIO Add Guide for Timed Audio {timed['id']}")
                _, guide_frame = matching[0]
                self.assertIsNotNone(guide_frame)
                self.assertEqual(guide_frame[1], timed["id"])
                self.assertEqual(guide_frame[2], 1, "Add Guide.frame_idx must come from Timed Audio.start_frame")

    def test_full_lock_examples_mux_exact_audio_not_sampled_audio(self):
        for path in self.workflows():
            wf = self.load(path)
            by_id = self.by_id(wf)
            locks = [n for n in wf["nodes"] if n["type"] in FULL_LOCK_TYPES]
            if not locks:
                continue
            self.assertEqual(len(locks), 1, path.name)
            lock = locks[0]
            consumers = [n for n in wf["nodes"] if n["type"] in {"CreateVideo", "MiniMaxH3LoopTrim"}]
            self.assertEqual(len(consumers), 1, path.name)
            row = self.incoming_link(wf, consumers[0], "audio")
            self.assertIsNotNone(row)
            self.assertEqual(row[1], lock["id"], f"{path.name}: final audio must come from full lock")
            self.assertEqual(row[2], 1, f"{path.name}: must use exact_audio output")
            self.assertNotIn("VAEDecodeAudio", [n["type"] for n in wf["nodes"]], f"{path.name}: full-lock final mux must not depend on sampled audio")

    def test_partial_lock_examples_finalize_exact_dialogue_after_sampling(self):
        for path in self.workflows():
            wf = self.load(path)
            locks = [n for n in wf["nodes"] if n["type"] in PARTIAL_LOCK_TYPES]
            if not locks:
                continue
            self.assertEqual(len(locks), 1, path.name)
            lock = locks[0]
            finalizers = [n for n in wf["nodes"] if n["type"] == "MiniMaxH3DialogueAudioFinalize"]
            self.assertEqual(len(finalizers), 1, path.name)
            finalizer = finalizers[0]
            by_id = self.by_id(wf)
            generated = self.incoming_link(wf, finalizer, "generated_audio")
            reference = self.incoming_link(wf, finalizer, "dialogue_reference_audio")
            manifest = self.incoming_link(wf, finalizer, "dialogue_lock_manifest")
            self.assertEqual(by_id[generated[1]]["type"], "VAEDecodeAudio")
            self.assertEqual((reference[1], reference[2]), (lock["id"], 1))
            self.assertEqual((manifest[1], manifest[2]), (lock["id"], 2))
            consumers = [n for n in wf["nodes"] if n["type"] in {"CreateVideo", "MiniMaxH3LoopTrim"}]
            self.assertEqual(len(consumers), 1, path.name)
            row = self.incoming_link(wf, consumers[0], "audio")
            self.assertEqual(row[1], finalizer["id"])
            self.assertEqual(row[2], 0)

    def test_context_loop_lock_is_between_context_and_sampler(self):
        for name in (
            "01_t2v_normal_scene_exact_audio_lock.json",
            "02_t2v_normal_scene_dialogue_audio_lock.json",
            "03_ref2v_basic_scene_exact_audio_lock.json",
        ):
            wf = self.load(WF_ROOT / "context_loop" / name)
            types = {node["type"]: node["id"] for node in wf["nodes"]}
            lock_type = "MiniMaxH3SceneDialogueAudioLock" if "dialogue" in name else "MiniMaxH3SceneExactAudioLock"
            pairs = {(row[1], row[3], row[5]) for row in wf["links"]}
            self.assertIn((types["MiniMaxH3ChainContext"], types[lock_type], "LATENT"), pairs)
            self.assertIn((types[lock_type], types["SamplerCustomAdvanced"], "LATENT"), pairs)


if __name__ == "__main__":
    unittest.main()
