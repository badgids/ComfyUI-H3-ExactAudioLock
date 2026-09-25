# Example workflows

The `example_workflows/` directory contains loadable, editable ComfyUI workflow JSON files for the supported ExactAudioLock integration patterns.

Every shipped example is required to satisfy two layout rules:

1. no node rectangle may overlap another node rectangle; and
2. the graph must remain readable left-to-right without moving nodes to reveal hidden nodes.

`tests/test_example_workflows.py` enforces node/link integrity, exact-audio output routing, dialogue finalizer routing, native Add Guide timing wiring, and node-layout overlap checks.

ExactAudioLock timed-audio Autogrow templates explicitly allow up to 100 inputs, ComfyUI's native hard limit; this avoids the default `TemplatePrefix` maximum of 10.

## Naming policy

The workflows do not rename ExactAudioLock, Context Loop, MiniMax H3, or Qwen3-TTS nodes. Their JSON uses each project's real registered node type and does not add custom `title` overrides to those nodes. ComfyUI therefore displays the upstream node's own display label.

Verified display labels include:

- `Audio Review / Accept Gate`
- `MiniMax H3 Timed Audio`
- `MiniMax H3 Exact Audio Lock`
- `MiniMax H3 Dialogue Audio Lock`
- `MiniMax H3 Dialogue Audio Finalize`
- `MiniMax H3 Scene Timed Audio`
- `MiniMax H3 Scene Exact Audio Lock`
- `MiniMax H3 Scene Dialogue Audio Lock`
- `Add Guide for MiniMax H3` from ComfyUI core (`MiniMaxH3AddGuide` internally)
- `🎨 Qwen3-TTS VoiceDesign` from `flybirdxx/ComfyUI-Qwen-TTS`
- `(Down)load Qwen TTS Models` and `Qwen TTS Voice Design Node` from `vantagewithai/Vantage-Nodes`
- `Qwen3-TTS Loader` and `Qwen3-TTS Custom Voice` from `DarioFT/ComfyUI-Qwen3-TTS`

## Verified upstream bases

The repaired examples are based on:

- ComfyUI-H3-ExactAudioLock baseline: `335fcbd67288156ce8527fbcf86134cf4191c2a1`
- Ethan Felty's Context Loop latest `main` at review time: `a8bb6c7b886312cc2821cd9959897d0088efdfe2`
- Flybird Qwen TTS: `c96e21027ae79bc9903863c55637456786313fd7`
- Vantage Nodes: `c048a74daab866002908e4dd2e01cc79d8f20857`
- DarioFT Qwen3-TTS: `17c22adb80a63c1c51bf74549e71a5cf218f4e1b`
- Official ComfyUI MiniMax H3 implementation verified with native `MiniMaxH3AddGuide` display name `Add Guide for MiniMax H3` and real input order `positive`, `vae`, `audio_vae`, `latent`, `image`, `audio`, `frame_idx`.

## Critical final-audio rule

The lock and sampler serve different purposes.

### Full Exact Audio Lock

`locked_av_latent` drives H3 generation, but `exact_audio` is the deterministic final soundtrack authority.

```text
Timed Audio ─► Exact Audio Lock ─► locked_av_latent ─► H3 sampler ─► video decode
                    │
                    └────────────► exact_audio ─────────────────────► final video/mux audio
```

Do not replace `exact_audio` with `VAEDecodeAudio` from the sampled H3 latent when the requirement is exact supplied waveform timing.

### Dialogue-partial lock

H3 may generate unsupplied audio, so the sampled H3 soundtrack is kept outside supplied dialogue. The supplied dialogue cores are restored exactly after sampling:

```text
Dialogue Audio Lock ─► dialogue_locked_av_latent ─► H3 sampler ─► VAEDecodeAudio ─┐
        │                                                                          │
        ├── dialogue_reference_audio ──────────────────────────────────────────────┤
        └── dialogue_lock_manifest ────────────────────────────────────────────────┤
                                                                                   ▼
                                                               Dialogue Audio Finalize
                                                                                   │
                                                                                   ▼
                                                                              final_audio
```

That final path is required for exact dialogue onset in the exported partial-lock soundtrack.

## Same-frame H3 conditioning

Standalone timed-dialogue examples use the Timed Audio node's `start_frame` output as the single frame source for native `Add Guide for MiniMax H3.frame_idx`:

```text
Approved AUDIO ────────┬────────► MiniMax H3 Timed Audio ─► timed_audio ─► lock
                       │                    │
                       │                    └── start_frame ─────────────┐
                       │                                                ▼
                       └────────────────────────────► Add Guide for MiniMax H3
```

The same AUDIO feeds both nodes. The frame is not duplicated manually in two widgets.

## Standalone workflows

| Workflow | Demonstrates |
| --- | --- |
| `standalone/01_t2v_exact_lock_load_audio.json` | Official-style H3 T2V + generic `LoadAudio` + same-frame native Add Guide + full exact lock + `exact_audio` final mux |
| `standalone/02_i2v_exact_lock_load_audio.json` | H3 I2V + exact supplied audio + exact final mux |
| `standalone/03_flf2v_exact_lock_load_audio.json` | H3 first/last-frame generation + exact supplied audio + exact final mux |
| `standalone/04_ref2v_exact_lock_load_audio.json` | Native H3 reference-to-video + exact supplied audio + exact final mux |
| `standalone/05_t2v_dialogue_audio_lock.json` | Dialogue-partial lock + same-frame Add Guide + H3-generated gaps + post-sampling Dialogue Audio Finalize |
| `standalone/06_t2v_exact_lock_multi_track.json` | Multiple independently timed/overlapping events, each with same-frame Add Guide conditioning |
| `standalone/07_t2v_audio_review_gate_connected.json` | Connected AUDIO candidate review -> approved Timed Audio -> Add Guide -> exact lock |
| `standalone/08_t2v_audio_review_gate_managed_files.json` | Managed-file review -> approved Timed Audio -> Add Guide -> exact lock |
| `standalone/09_t2v_legacy_single_audio_input.json` | Backward-compatible legacy single-AUDIO input with matching native Add Guide frame |
| `standalone/10_t2v_exact_lock_with_minimax_h3_add_guide.json` | Focused native Add Guide + Timed Audio single-source-of-truth example |

The single-event Timed Audio examples intentionally use a nonzero start frame so a frame-0 regression is visible immediately.

The image/audio loader templates use placeholder filenames such as `first_frame.png` and `dialogue.wav`. Put matching files in `ComfyUI/input`, or choose your own input files after loading the workflow.

## Qwen3-TTS workflows

| Workflow | Required pack |
| --- | --- |
| `qwen3_tts/01_flybird_voice_design_review_exact_lock.json` | `flybirdxx/ComfyUI-Qwen-TTS` |
| `qwen3_tts/02_vantage_voice_design_exact_lock.json` | `vantagewithai/Vantage-Nodes` |
| `qwen3_tts/03_dario_custom_voice_exact_lock.json` | `DarioFT/ComfyUI-Qwen3-TTS` |

Each Qwen workflow sends the generated/approved AUDIO to `MiniMax H3 Timed Audio`, uses Timed Audio's `start_frame` output to drive native `Add Guide for MiniMax H3.frame_idx`, sends `timed_audio` into `MiniMax H3 Exact Audio Lock`, and muxes `exact_audio` as the final soundtrack.

The Flybird example still reviews two real `🎨 Qwen3-TTS VoiceDesign` candidates and passes only the approved take downstream.

### Built-in ComfyUI Qwen3-TTS

No built-in Qwen3-TTS generation workflow is included because current ComfyUI core does not expose a core Qwen3-TTS generation node. Qwen-related core nodes for other model families are not renamed or treated as TTS nodes.

## Context Loop workflows

| Workflow | Demonstrates |
| --- | --- |
| `context_loop/01_t2v_normal_scene_exact_audio_lock.json` | Current 0.6 T2V Normal topology + Scene Exact Audio Lock + scene `exact_audio` into Loop Trim |
| `context_loop/02_t2v_normal_scene_dialogue_audio_lock.json` | Current 0.6 T2V Normal topology + Scene Dialogue Audio Lock + post-sampling Dialogue Audio Finalize into Loop Trim |
| `context_loop/03_ref2v_basic_scene_exact_audio_lock.json` | Current 0.6 Ref2V Basic-style topology + native reference conditioning + scene exact lock + scene `exact_audio` into Loop Trim |

The scene lock remains between Context Loop's post-context latent and the sampler:

```text
MiniMax H3 Chain Context.latent
        -> scene audio lock
        -> SamplerCustomAdvanced.latent_image
```

Current scene routing remains:

```text
MiniMax H3 Chain Current.clip_index
        -> scene lock.current_scene
```

The **audio output path** is different for the two lock modes:

```text
Full scene lock:
Scene Exact Audio Lock.exact_audio -> MiniMax H3 Loop Trim.audio

Dialogue-partial scene lock:
Sampler -> VAEDecodeAudio --------------------\
Scene Dialogue Lock.dialogue_reference_audio ---+-> Dialogue Audio Finalize.final_audio -> MiniMax H3 Loop Trim.audio
Scene Dialogue Lock.dialogue_lock_manifest ----/
```

Do not enable a separate complete Context Loop source-audio target lock on the same sampler path as `MiniMax H3 Scene Exact Audio Lock`. There should be one owner of the complete target-audio latent.

## Models

The examples use the current official MiniMax H3 local model filenames already used by the repository templates:

- `minimax_h3_fl2va_pruned_int8_convrot.safetensors`
- `minimax_h3_ref2va_pruned_int8_convrot.safetensors`
- `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors`
- `minimax_h3_video_vae_fp16.safetensors`
- `minimax_h3_audio_vae_fp32.safetensors`

If your installation uses another supported quantization or filename, choose that model in the corresponding loader after opening the workflow.
## Batch dialogue / Context Loop examples

Two additional component workflows demonstrate the production-wide dialogue path:

- `context_loop/04_dialogue_timeline_review_board.json`
  - Context Loop Plan (Modern) with `<d>...</d>` dialogue;
  - three example `LoadAudio` inputs;
  - `MiniMax H3 Dialogue Timeline`;
  - one `Dialogue Review / Approval Board` that reviews all required lines.

- `context_loop/05_context_loop_current_scene_dialogue.json`
  - the same production-wide preflight;
  - `MiniMax H3 Chain Loop Start` and `MiniMax H3 Chain Current`;
  - `Chain Current.clip_index -> MiniMax H3 Current Scene Dialogue.current_scene`;
  - the approved production set filtered to one current-scene event set.

The second example is intentionally the routing component that is inserted ahead
of a normal Context Loop scene lock. Connect
`current_scene_dialogue_set -> MiniMax H3 Scene Dialogue Audio Lock.dialogue_event_set`
or the equivalent Scene Exact Audio Lock input in the sampling workflow.

See `DIALOGUE_REVIEW_BOARD.md` for the complete production path.
