# Example workflows

The `example_workflows/` directory contains loadable ComfyUI workflow JSON files for the supported ExactAudioLock integration patterns.

## Naming policy

The workflows do not rename the ExactAudioLock or Qwen3-TTS nodes. Their JSON uses each project's real registered node type, and the workflow does not add a custom `title` override to those nodes. ComfyUI therefore displays the node's own upstream display label.

Examples include the following verified upstream display labels:

- `Audio Review / Accept Gate`
- `MiniMax H3 Timed Audio`
- `MiniMax H3 Exact Audio Lock`
- `MiniMax H3 Dialogue Audio Lock`
- `MiniMax H3 Scene Timed Audio`
- `MiniMax H3 Scene Exact Audio Lock`
- `MiniMax H3 Scene Dialogue Audio Lock`
- `🎨 Qwen3-TTS VoiceDesign` from `flybirdxx/ComfyUI-Qwen-TTS`
- `(Down)load Qwen TTS Models` and `Qwen TTS Voice Design Node` from `vantagewithai/Vantage-Nodes`
- `Qwen3-TTS Loader` and `Qwen3-TTS Custom Voice` from `DarioFT/ComfyUI-Qwen3-TTS`

## Current upstream bases

These examples were prepared against:

- ComfyUI-H3-ExactAudioLock: `0bf579e06e9dbbfff81adb4a4b5c593124fde34e`
- Ethan Felty's Context Loop: `a8bb6c7b886312cc2821cd9959897d0088efdfe2`
- Flybird Qwen TTS: `c96e21027ae79bc9903863c55637456786313fd7`
- Vantage Nodes: `c048a74daab866002908e4dd2e01cc79d8f20857`
- DarioFT Qwen3-TTS: `17c22adb80a63c1c51bf74549e71a5cf218f4e1b`
- Official ComfyUI MiniMax H3 workflow templates from `Comfy-Org/workflow_templates` at `90c71fb78b3726392d010ff62a8e79e92d7296ad`

## Standalone workflows

| Workflow | Demonstrates |
| --- | --- |
| `standalone/01_t2v_exact_lock_load_audio.json` | Official-style H3 T2V + generic `LoadAudio` + Timed Audio + full Exact Audio Lock |
| `standalone/02_i2v_exact_lock_load_audio.json` | H3 I2V + exact supplied audio |
| `standalone/03_flf2v_exact_lock_load_audio.json` | H3 first/last-frame generation + exact supplied audio |
| `standalone/04_ref2v_exact_lock_load_audio.json` | Native H3 reference-to-video + exact supplied audio |
| `standalone/05_t2v_dialogue_audio_lock.json` | Dialogue-only partial lock while H3 generates unsupplied sound |
| `standalone/06_t2v_exact_lock_multi_track.json` | Multiple independently timed/overlapping audio events |
| `standalone/07_t2v_audio_review_gate_connected.json` | Two normal AUDIO candidates -> review/accept -> exact lock |
| `standalone/08_t2v_audio_review_gate_managed_files.json` | Gate managed-file mode; select files from ComfyUI input before queueing |
| `standalone/09_t2v_legacy_single_audio_input.json` | Backward-compatible legacy single-AUDIO input on Exact Audio Lock |
| `standalone/10_t2v_exact_lock_with_minimax_h3_add_guide.json` | Exact Audio Lock plus the native `MiniMax H3 Add Guide` using the same dialogue AUDIO at the same target frame |

The image/audio loader templates use obvious placeholder filenames such as `first_frame.png` and `dialogue.wav`. Put files with those names into `ComfyUI/input`, or select your own file in the loaded nodes before queueing.

## Qwen3-TTS workflows

| Workflow | Required pack |
| --- | --- |
| `qwen3_tts/01_flybird_voice_design_review_exact_lock.json` | `flybirdxx/ComfyUI-Qwen-TTS` |
| `qwen3_tts/02_vantage_voice_design_exact_lock.json` | `vantagewithai/Vantage-Nodes` |
| `qwen3_tts/03_dario_custom_voice_exact_lock.json` | `DarioFT/ComfyUI-Qwen3-TTS` |

The Flybird example generates two takes with the real `🎨 Qwen3-TTS VoiceDesign` node, sends them through `Audio Review / Accept Gate`, and passes only the approved take to `MiniMax H3 Timed Audio`.

The Vantage and DarioFT examples use their real current node registrations and current widget ordering.

### Built-in ComfyUI Qwen3-TTS

No built-in Qwen3-TTS workflow is included because the current ComfyUI core source checked for these templates does not expose a core Qwen3-TTS generation node. Qwen-related core nodes that exist for other model families are not renamed or treated as TTS nodes.

## Context Loop workflows

| Workflow | Demonstrates |
| --- | --- |
| `context_loop/01_t2v_normal_scene_exact_audio_lock.json` | Current 0.6 T2V Normal topology + `MiniMax H3 Scene Exact Audio Lock` |
| `context_loop/02_t2v_normal_scene_dialogue_audio_lock.json` | Current 0.6 T2V Normal topology + `MiniMax H3 Scene Dialogue Audio Lock` |
| `context_loop/03_ref2v_basic_scene_exact_audio_lock.json` | Current 0.6 Ref2V Basic-style topology + native reference conditioning + scene exact lock |

Both Context Loop examples follow the current integration point:

`MiniMax H3 Chain Context.latent -> scene audio lock -> SamplerCustomAdvanced.latent_image`

and wire:

`MiniMax H3 Chain Current.clip_index -> current_scene`

The scene-aware examples use two `MiniMax H3 Scene Timed Audio` nodes to demonstrate a two-scene schedule. Replace `context_scene_1.wav` and `context_scene_2.wav` with your own approved sources.

Do not enable a separate complete Context Loop source-audio target lock on the same sampler path as `MiniMax H3 Scene Exact Audio Lock`. There should be one owner of the complete target audio latent.

## Models

The standalone examples use the current official MiniMax H3 local model filenames:

- `minimax_h3_fl2va_pruned_int8_convrot.safetensors`
- `minimax_h3_ref2va_pruned_int8_convrot.safetensors`
- `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors`
- `minimax_h3_video_vae_fp16.safetensors`
- `minimax_h3_audio_vae_fp32.safetensors`

If your installation uses another supported quantization or filename, select that model in the loader after opening the workflow.
