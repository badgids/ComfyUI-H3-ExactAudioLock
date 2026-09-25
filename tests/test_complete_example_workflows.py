import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WF_ROOT = ROOT / "example_workflows"


class CompleteExampleWorkflowTests(unittest.TestCase):
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

    def test_every_example_is_complete_video_audio_generation_workflow(self):
        for path in self.workflows():
            with self.subTest(path=path.relative_to(ROOT)):
                wf = self.load(path)
                types = [node["type"] for node in wf["nodes"]]
                for required in ("UNETLoader", "CLIPLoader", "SamplerCustomAdvanced"):
                    self.assertIn(required, types, f"{path.name}: missing {required}")
                self.assertGreaterEqual(
                    types.count("VAELoader"),
                    2,
                    f"{path.name}: requires video + audio VAE loaders",
                )
                self.assertTrue(
                    {"MiniMaxH3ImageToVideo", "MiniMaxH3ReferenceToVideo"} & set(types),
                    f"{path.name}: missing MiniMax H3 latent builder",
                )
                if "context_loop" in path.parts:
                    self.assertIn("MiniMaxH3ChainLoopStart", types, f"{path.name}: missing Context Loop start")
                    self.assertIn("MiniMaxH3ChainLoopEnd", types, f"{path.name}: missing Context Loop end")
                    self.assertIn("MiniMaxH3ChainAssemble", types, f"{path.name}: missing final video assembly")
                else:
                    self.assertIn("CreateVideo", types, f"{path.name}: missing final video creation")
                    self.assertIn("SaveVideo", types, f"{path.name}: missing final video save")

    def test_batch_dialogue_context_loop_examples_are_end_to_end(self):
        cases = {
            "04_dialogue_timeline_review_board.json": "MiniMaxH3SceneDialogueAudioLock",
            "05_context_loop_current_scene_dialogue.json": "MiniMaxH3SceneExactAudioLock",
        }
        for name, lock_type in cases.items():
            with self.subTest(name=name):
                wf = self.load(WF_ROOT / "context_loop" / name)
                by_id = self.by_id(wf)
                types = {node["type"]: node["id"] for node in wf["nodes"]}
                pairs = {(row[1], row[3], row[5]) for row in wf["links"]}

                for required in (
                    "MiniMaxH3DialogueTimeline",
                    "H3ExactAudioLockDialogueReviewBoard",
                    "MiniMaxH3CurrentSceneDialogue",
                    "MiniMaxH3ChainContext",
                    lock_type,
                    "SamplerCustomAdvanced",
                    "MiniMaxH3ChainAssemble",
                ):
                    self.assertIn(required, types, f"{name}: missing {required}")

                self.assertIn(
                    (types["MiniMaxH3ChainContext"], types[lock_type], "LATENT"),
                    pairs,
                )
                self.assertIn(
                    (types[lock_type], types["SamplerCustomAdvanced"], "LATENT"),
                    pairs,
                )
                self.assertIn(
                    (types["MiniMaxH3CurrentSceneDialogue"], types[lock_type], "H3_DIALOGUE_EVENT_SET"),
                    pairs,
                )

                # Current Scene Dialogue must be driven by Chain Current.clip_index.
                selector = by_id[types["MiniMaxH3CurrentSceneDialogue"]]
                current_scene_input = next(i for i in selector["inputs"] if i["name"] == "current_scene")
                link = next(row for row in wf["links"] if row[0] == current_scene_input["link"])
                self.assertEqual(link[1], types["MiniMaxH3ChainCurrent"])
                self.assertEqual(link[2], 1)


if __name__ == "__main__":
    unittest.main()
