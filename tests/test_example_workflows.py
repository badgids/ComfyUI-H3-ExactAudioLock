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
    "MiniMaxH3SceneTimedAudio",
    "MiniMaxH3SceneExactAudioLock",
    "MiniMaxH3SceneDialogueAudioLock",
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

class ExampleWorkflowTests(unittest.TestCase):
    def workflows(self):
        files = sorted(WF_ROOT.rglob("*.json"))
        self.assertTrue(files, "no example workflows found")
        return files

    def test_all_json_loads_and_links_are_consistent(self):
        for path in self.workflows():
            with self.subTest(path=path.relative_to(ROOT)):
                wf = json.loads(path.read_text(encoding="utf-8"))
                nodes = wf["nodes"]
                links = wf["links"]
                by_id = {n["id"]: n for n in nodes}
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

    def test_real_node_labels_are_not_overridden(self):
        for path in self.workflows():
            wf = json.loads(path.read_text(encoding="utf-8"))
            for node in wf["nodes"]:
                if node["type"] in NO_TITLE_TYPES:
                    self.assertNotIn(
                        "title", node,
                        f"{path.name}: {node['type']} must use its upstream display label"
                    )

    def test_coverage_matrix(self):
        present=set()
        for path in self.workflows():
            wf=json.loads(path.read_text(encoding="utf-8"))
            present.update(n["type"] for n in wf["nodes"])
        missing=REQUIRED_TYPES-present
        self.assertFalse(missing, f"missing required workflow node coverage: {sorted(missing)}")

    def test_context_loop_lock_is_between_context_and_sampler(self):
        for name in (
            "01_t2v_normal_scene_exact_audio_lock.json",
            "02_t2v_normal_scene_dialogue_audio_lock.json",
        ):
            wf=json.loads((WF_ROOT/"context_loop"/name).read_text(encoding="utf-8"))
            nodes={n["id"]:n for n in wf["nodes"]}
            types={n["type"]:n["id"] for n in wf["nodes"]}
            lock_type=("MiniMaxH3SceneDialogueAudioLock"
                       if "dialogue" in name else "MiniMaxH3SceneExactAudioLock")
            context_id=types["MiniMaxH3ChainContext"]
            lock_id=types[lock_type]
            sampler_id=types["SamplerCustomAdvanced"]
            pairs={(row[1],row[3],row[5]) for row in wf["links"]}
            self.assertIn((context_id,lock_id,"LATENT"),pairs)
            self.assertIn((lock_id,sampler_id,"LATENT"),pairs)

if __name__ == "__main__":
    unittest.main()
