# ComfyUI-H3-ExactAudioLock

**Author/Developer:** [Alan Guice (Badgids)](https://github.com/badgids)<br>
**Copyright:** © 2026 Alan Guice (Badgids)<br>
**License:** [MIT License](LICENSE)

ComfyUI-H3-ExactAudioLock is a ComfyUI custom-node package for MiniMax H3 that builds deterministic, sample-accurate target audio, supports full or dialogue-only audio locking, and provides a human Audio Review / Accept Gate for choosing one approved take before it continues through the workflow.

It is designed for dialogue, multi-speaker scenes, overlapping speech, singing, music-video work, recursive H3 workflows, and other MiniMax H3 generations where the picture needs to respond to exact user-supplied audio instead of regenerated or approximate audio.

> [!NOTE]
> ComfyUI-H3-ExactAudioLock is an independent community custom node. It is not an official ComfyUI or MiniMax project.

## Table of contents

- [Requirements](#requirements)
- [Installation](#installation)
  - [Install with Git](#install-with-git)
  - [Install the Python requirements](#install-the-python-requirements)
  - [Install from a ZIP](#install-from-a-zip)
  - [Verify the installation](#verify-the-installation)
- [Features](#features)
- [Nodes](#nodes)
  - [Audio Review / Accept Gate](#audio-review--accept-gate)
  - [MiniMax H3 Timed Audio](#minimax-h3-timed-audio)
  - [MiniMax H3 Exact Audio Lock](#minimax-h3-exact-audio-lock)
  - [MiniMax H3 Dialogue Audio Lock](#minimax-h3-dialogue-audio-lock)
  - [MiniMax H3 Scene Timed Audio](#minimax-h3-scene-timed-audio)
  - [MiniMax H3 Scene Exact Audio Lock](#minimax-h3-scene-exact-audio-lock)
  - [MiniMax H3 Scene Dialogue Audio Lock](#minimax-h3-scene-dialogue-audio-lock)
- [Audio review workflow](#audio-review-workflow)
- [Example workflows](#example-workflows)
- [Basic H3 usage](#basic-h3-usage)
- [Unlimited timed audio inputs](#unlimited-timed-audio-inputs)
- [Layering and overlapping audio](#layering-and-overlapping-audio)
- [How timing works](#how-timing-works)
- [Multi-speaker lip sync](#multi-speaker-lip-sync)
- [Scene-aware recursive dialogue](#scene-aware-recursive-dialogue)
- [Technical notes](#technical-notes)
- [Documentation](#documentation)
- [Troubleshooting](#troubleshooting)
- [Repository structure](#repository-structure)
- [Acknowledgements](#acknowledgements)
- [Author and attribution](#author-and-attribution)
- [License](#license)

## Requirements

- A current ComfyUI installation with MiniMax H3 support.
- A ComfyUI build with the current `comfy_api.latest` node API and native `Autogrow`.
- MiniMax H3 joint AV latents and the MiniMax H3 audio VAE for the lock nodes.
- Python 3.11+ in the environment that runs ComfyUI.
- PyTorch and TorchAudio from that same ComfyUI environment.
- PyAV `14.2.0` or newer for managed audio-file review.

This repository includes a standard [`requirements.txt`](requirements.txt). Install it with the **same Python interpreter or virtual environment that runs ComfyUI**.

The requirements file intentionally does **not** redeclare `torch` or `torchaudio`. ComfyUI installs those packages for the user's CPU/GPU backend, and blindly reinstalling them from a custom-node requirements file can replace a working CUDA, ROCm, XPU, or other platform-specific build.

Current ComfyUI also includes PyAV, but this node pack declares its direct PyAV requirement explicitly so manual installs and dependency-aware custom-node installers can satisfy the gate's managed-file mode consistently.

See [docs/INSTALLATION.md](docs/INSTALLATION.md) for environment-specific installation commands and dependency policy.

## Installation

### Install with Git

Clone the repository into ComfyUI's `custom_nodes` directory:

```bash
cd /path/to/ComfyUI/custom_nodes
git clone https://github.com/badgids/ComfyUI-H3-ExactAudioLock.git
cd ComfyUI-H3-ExactAudioLock
```

### Install the Python requirements

Use the Python interpreter that actually runs ComfyUI.

If ComfyUI uses a `.venv` inside the ComfyUI directory on Linux, WSL2, or macOS:

```bash
../../.venv/bin/python -m pip install -r requirements.txt
```

If that environment is already activated:

```bash
python -m pip install -r requirements.txt
```

For a normal Windows virtual environment:

```powershell
..\..\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

For the standard ComfyUI Windows portable layout:

```powershell
..\..\..\python_embeded\python.exe -m pip install -r requirements.txt
```

Installers that honor a custom node's `requirements.txt` may install this dependency automatically. The commands above are the explicit manual fallback.

Restart ComfyUI after installation.

### Install from a ZIP

1. Download and extract the repository.
2. Place the extracted `ComfyUI-H3-ExactAudioLock` directory under `ComfyUI/custom_nodes/`.
3. Open a terminal in that directory.
4. Run `python -m pip install -r requirements.txt` with the same Python interpreter/environment that runs ComfyUI.
5. Restart ComfyUI.

The installed directory should contain at least:

```text
ComfyUI/
└── custom_nodes/
    └── ComfyUI-H3-ExactAudioLock/
        ├── docs/
        ├── tests/
        ├── web/
        ├── __init__.py
        ├── audio_review_gate.py
        ├── requirements.txt
        ├── README.md
        └── LICENSE
```

### Verify the installation

Using the ComfyUI Python environment, verify the required audio packages:

```bash
python -c "import torch, torchaudio, av; print('torch', torch.__version__); print('torchaudio', torchaudio.__version__); print('av', av.__version__)"
```

Then start or restart ComfyUI and confirm these nodes are available under `MiniMax H3/Audio`.

For full installation details, Windows portable examples, upgrades, and dependency troubleshooting, read [docs/INSTALLATION.md](docs/INSTALLATION.md).

## Features

- Locks an exact user-controlled waveform into MiniMax H3's target audio latent.
- Keeps H3 video denoisable while the complete target audio stream remains fixed.
- Supports dialogue-only partial locking so H3 can still generate ambience, Foley, music, effects, and other unsupplied audio.
- Provides scene-aware variants for recursive H3 Director / Context Loop workflows.
- Provides an embedded Audio Review / Accept Gate for choosing one candidate and optionally saving alternate takes.
- Accepts normal ComfyUI `AUDIO` from TTS, voice-cloning, music, audio-loader, or processing nodes.
- Reviews WAV, MP3, FLAC, OGG/OGA, and Opus files from ComfyUI's managed input directory.
- Accepts an effectively unlimited number of timed audio events through ComfyUI's native `Autogrow` inputs.
- Places every source on H3's 24 fps target-video timeline using deterministic integer frame-to-sample conversion.
- Mixes multiple speakers or sound sources sample-accurately into one H3 target waveform.
- Supports overlapping dialogue and layered audio.
- Resamples lock sources to the MiniMax H3 audio VAE sample rate before mixing.
- Uses waveform-domain digital silence rather than zero-valued latent padding.
- Provides per-source gain control and optional speaker/event labels.
- Provides deterministic overlap and overflow policies.
- Returns diagnostic manifests for reproducibility and troubleshooting.
- Preserves the original single-audio input as a backward-compatible path for older workflows.
- Can be used with ComfyUI's core `MiniMaxH3AddGuide` for event-level audio reinforcement at the same target frame.

## Nodes

### Audio Review / Accept Gate

**Class ID:** `H3ExactAudioLockAudioReviewGate`

A Context-Loop-style human review gate for generic audio candidates. It presents one candidate at a time, lets the user move backward and forward through the current candidate set, optionally saves alternate takes, and sends only the selected approved candidate downstream.

The gate supports two source modes:

- `connected_audio` accepts normal ComfyUI `AUDIO`. Use `audio` for the first candidate and the Autogrow `audio_candidates` sockets for additional candidates. Batched `AUDIO` is split into individual review takes.
- `audio_file` reviews managed ComfyUI input files. Use `audio_file` for the first candidate and the Autogrow `audio_files` sockets for additional candidates. Supported formats are WAV, MP3, FLAC, OGG/OGA, and Opus.

| Input | Purpose |
| --- | --- |
| `source_mode` | Choose connected `AUDIO` or managed audio-file candidates. |
| `audio` | Optional first connected `AUDIO` candidate. |
| `audio_candidates` | Autogrow group of additional connected `AUDIO` candidates. |
| `audio_file` | Optional first managed WAV/MP3/FLAC/OGG/OGA/Opus candidate. |
| `audio_files` | Autogrow group of additional managed file candidates. |
| `review_label` | Optional label shown above the review carousel. |
| `delete_rejected_audio_files` | Deletes only unselected/unkept managed input files after an explicit decision. It never infers a source path from connected `AUDIO`. |
| `review_timeout_seconds` | Maximum time to wait for the browser review decision. |

| Output | Purpose |
| --- | --- |
| `accepted_audio` | Only the selected approved candidate as normal ComfyUI `AUDIO`. |

Marked alternates are saved separately under:

```text
ComfyUI/output/h3_exact_audio_lock_review/alternates/<review-token>/
```

The selected take is not duplicated as an alternate, rejected candidates are not bundled into the output, and resolved review state is removed before the next loop/requeue iteration.

See [docs/AUDIO_REVIEW_GATE.md](docs/AUDIO_REVIEW_GATE.md) for the complete candidate, retention, deletion, cleanup, caching, and loop-isolation contract.

### MiniMax H3 Timed Audio

**Class ID:** `MiniMaxH3TimedAudio`

Wraps one normal ComfyUI `AUDIO` value with its target placement and source metadata.

| Input | Purpose |
| --- | --- |
| `audio` | One dialogue line, singing passage, music section, or other audio event. |
| `start_frame` | Exact pixel-frame index on MiniMax H3's 24 fps target-video timeline. |
| `gain_db` | Gain applied before mixing. `0.0` dB preserves the source level. |
| `label` | Optional speaker or event label written to the mix manifest. |

Create one Timed Audio node for each independently placed event.

### MiniMax H3 Exact Audio Lock

**Class ID:** `MiniMaxH3ExactAudioLock`

Accepts the H3 AV latent, MiniMax H3 audio VAE, and any number of `MiniMax H3 Timed Audio` inputs. It builds one exact target waveform, encodes it once, replaces H3's target audio latent, locks the entire audio stream against denoising, and leaves video denoisable.

| Input | Purpose |
| --- | --- |
| `av_latent` | Joint MiniMax H3 video/audio target latent. |
| `audio_vae` | MiniMax H3 audio VAE. |
| `timed_audios` | Native ComfyUI `Autogrow` input for Timed Audio events. |
| `mix_policy` | Controls overlapping sources. |
| `overflow_policy` | Controls sources that extend beyond the H3 target timeline. |
| `audio` | Optional legacy single-audio input for older workflows. |
| `legacy_start_frame` | Target frame for the optional legacy audio input. |

| Output | Purpose |
| --- | --- |
| `locked_av_latent` | H3 AV latent with the exact complete audio target inserted and locked. |
| `exact_audio` | Exact waveform mixed and encoded into H3. |
| `mix_manifest` | Deterministic JSON describing placement, gains, sample counts, crop/scale decisions, and peaks. |

### MiniMax H3 Dialogue Audio Lock

**Class ID:** `MiniMaxH3DialogueAudioLock`

A partial lock for non-recursive workflows. It protects supplied dialogue intervals and configured margins while leaving the rest of the H3 audio target available for generation.

Use it when speech must remain exact but H3 should still generate room tone, ambience, music, footsteps, impacts, and other scene sound.

Its `dialogue_reference_audio` output is a reference stem, not the finished soundtrack. Use the sampled H3 audio for the final mix.

See [docs/DIALOGUE_PARTIAL_LOCK.md](docs/DIALOGUE_PARTIAL_LOCK.md).

### MiniMax H3 Scene Timed Audio

**Class ID:** `MiniMaxH3SceneTimedAudio`

Adds a one-based `scene_index` to an approved audio event plus its scene-local start frame, gain, and label. It lets one recursive graph carry a complete schedule while only the current scene's events become active.

See [docs/SCENE_DIALOGUE.md](docs/SCENE_DIALOGUE.md).

### MiniMax H3 Scene Exact Audio Lock

**Class ID:** `MiniMaxH3SceneExactAudioLock`

The recursive full-lock variant. It selects only the `MiniMax H3 Scene Timed Audio` events assigned to `current_scene`, builds the exact scene target, and locks the complete H3 audio target for that iteration.

`MiniMax H3 Chain Current.clip_index` can be wired directly to `current_scene`.

See [docs/SCENE_DIALOGUE.md](docs/SCENE_DIALOGUE.md).

### MiniMax H3 Scene Dialogue Audio Lock

**Class ID:** `MiniMaxH3SceneDialogueAudioLock`

The recursive partial-lock variant. It selects only the current scene's dialogue events, protects those dialogue intervals and margins, and leaves the rest of the scene audio generative.

For normal film production, `empty_scene_policy=generate` allows scenes without dialogue to pass through and lets H3 create their sound normally.

See [docs/DIALOGUE_PARTIAL_LOCK.md](docs/DIALOGUE_PARTIAL_LOCK.md) and [docs/SCENE_DIALOGUE.md](docs/SCENE_DIALOGUE.md).

## Audio review workflow

The review gate belongs **before** the Timed Audio / ExactAudioLock stage. Generate or load multiple candidate takes, approve exactly one, and then schedule that approved `AUDIO` on the H3 timeline.

```text
Qwen3-TTS take A --------\
Qwen3-TTS take B ---------\
YuE2 / ACE-Step take C ----+--> Audio Review / Accept Gate
Loaded audio take D -------/              |
                                            +---- "Save this take as an alternate"
                                            |       writes marked non-winning takes
                                            |       to ComfyUI/output/...
                                            |
                                            +---- accepted_audio = ONE selected take
                                                         |
                                                         v
                                               MiniMax H3 Timed Audio
                                               (start_frame / gain / label)
                                                         |
                                                         v
                                             MiniMax H3 Exact Audio Lock
                                                         |
                                                         v
                                                  locked_av_latent
                                                         |
                                                         v
                                                     H3 sampler
```

For dialogue-only H3 soundscape generation, replace `MiniMax H3 Exact Audio Lock` with `MiniMax H3 Dialogue Audio Lock`.

For recursive/Director workflows:

```text
approved take
    |
    v
MiniMax H3 Scene Timed Audio
    |        scene_index + local start_frame
    v
MiniMax H3 Scene Exact Audio Lock
    ^        or Scene Dialogue Audio Lock
    |
MiniMax H3 Chain Current.clip_index
```

The gate does not control another TTS/music node pack's private reroll logic. To review several generated takes at once, present those takes to the gate through its candidate sockets, a batched `AUDIO`, or managed file candidates.

## Example workflows

Loadable, editable ComfyUI workflows are included under [`example_workflows/`](example_workflows/).

They cover the standalone official-style MiniMax H3 T2V, I2V, first/last-frame, and Ref2V paths; full Exact Audio Lock; dialogue-only partial locking; multi-track timing; connected and managed-file review; the legacy single-AUDIO input; native `MiniMax H3 Add Guide`; Qwen3-TTS integrations; and current H3 Context Loop scene-aware full and dialogue-only locking.

The Qwen3-TTS examples use the real upstream nodes from `flybirdxx/ComfyUI-Qwen-TTS`, `vantagewithai/Vantage-Nodes`, and `DarioFT/ComfyUI-Qwen3-TTS`.

The workflow JSON uses each node's real registered type and does **not** add a custom title override to the ExactAudioLock or Qwen3-TTS nodes. ComfyUI therefore shows the node's actual upstream display label, including labels such as `🎨 Qwen3-TTS VoiceDesign`, `Qwen TTS Voice Design Node`, and `Qwen3-TTS Custom Voice`.

No built-in ComfyUI Qwen3-TTS example is included because the current ComfyUI core checked for these templates does not expose a core Qwen3-TTS generation node.

The Context Loop examples use Ethan Felty's current 0.6 T2V Normal and Ref2V Basic topology and place the scene lock between `MiniMax H3 Chain Context.latent` and `SamplerCustomAdvanced.latent_image`, with `MiniMax H3 Chain Current.clip_index` driving `current_scene`.

See [docs/EXAMPLE_WORKFLOWS.md](docs/EXAMPLE_WORKFLOWS.md) for the complete workflow matrix, required node packs, upstream revisions, model filenames, placeholder input files, and Context Loop wiring notes.

## Basic H3 usage

### Ref2VA workflow

ComfyUI already creates the joint H3 audio/video latent. This package does **not** replace or duplicate ComfyUI's native H3 latent-creation nodes.

For Ref2VA, use ComfyUI's native `MiniMax H3 Reference to Video` node. Its `latent` output is the joint MiniMax H3 AV latent expected by the lock nodes.

```text
MiniMax H3 Reference to Video (Ref2VA)
    ├── positive ───────────────────────────────► normal H3 conditioning path
    └── latent ───────────────┐
                              ▼
                     MiniMax H3 Exact Audio Lock
                              │
                              └── locked_av_latent ─────────────► H3 sampler
```

Then add timed audio:

1. Create the normal H3 Ref2VA workflow.
2. Connect the native H3 joint `latent` to `MiniMax H3 Exact Audio Lock.av_latent`.
3. Connect the MiniMax H3 audio VAE to `audio_vae`.
4. Generate, load, and optionally review each dialogue, vocal, music, or other audio source.
5. Connect each approved source to its own `MiniMax H3 Timed Audio` node.
6. Set each event's exact `start_frame`, optional `gain_db`, and label.
7. Connect every Timed Audio output to the Autogrow `timed_audios` inputs.
8. Select the desired `mix_policy` and `overflow_policy`.
9. Connect `locked_av_latent` to the sampler instead of the original unlocked H3 latent.
10. Continue the normal H3 sampling and decoding workflow.
11. Use `exact_audio` when you need the exact mixed waveform for muxing, review, or verification.
12. Inspect `mix_manifest` when diagnosing placement, gain, overlap, or overflow behavior.

### Using `MiniMaxH3AddGuide` at the same time

`MiniMax H3 Exact Audio Lock` controls the H3 target-audio latent. `MiniMaxH3AddGuide` controls event-level conditioning. They can be used together.

```text
Approved Speaker AUDIO
        |-----------------------------> MiniMaxH3AddGuide @ exact frame
        |
        +-> MiniMax H3 Timed Audio ----> MiniMax H3 Exact Audio Lock
                                             |
                                      locked target audio
                                             |
                                           H3
```

For visible non-speaking characters, explicitly instruct H3 that their mouths remain closed during the other speaker's line.

### Other native H3 modes

If you are not using Ref2VA, connect the joint AV `LATENT` produced by the appropriate native ComfyUI MiniMax H3 node, such as `MiniMax H3 Image to Video` or `Empty MiniMax H3 AV Latent`.

The lock node expects an H3 joint AV latent, not a standalone image/video latent.

## Unlimited timed audio inputs

`MiniMaxH3ExactAudioLock` uses ComfyUI's native `Autogrow` socket mechanism and does not impose a custom speaker or utterance limit.

```text
Speaker 1 AUDIO -> MiniMax H3 Timed Audio --\
Speaker 2 AUDIO -> MiniMax H3 Timed Audio ----\
Speaker 3 AUDIO -> MiniMax H3 Timed Audio ------> MiniMax H3 Exact Audio Lock
Speaker 4 AUDIO -> MiniMax H3 Timed Audio ----/
...                                         --/
```

Practical limits are the ComfyUI graph size and available system resources.

## Layering and overlapping audio

Overlapping sources are supported.

```text
24 fps target timeline

Pippa     ----[ dialogue A ]---------------------------
Magnus           ----[ dialogue B ]--------------------
Cricket                    --[ dialogue C ]------------

Mixed bus ----[ A + overlap(A,B) + overlap(B,C) ]------
```

Choose one `mix_policy`:

- `sum` preserves exact arithmetic summing.
- `prevent_clipping` mixes normally, then applies one deterministic global scale only when the finished bus peak exceeds `0.999`.
- `reject_overlap` treats overlapping source intervals as an error.

Choose one `overflow_policy`:

- `error` fails if an event starts outside or extends beyond the H3 target timeline.
- `crop` deliberately crops an event at the target boundary.

For dialogue production, `sum` + `error` is a useful strict default: overlapping speakers are allowed, but speech is not silently cut off at the end of the clip.

## How timing works

MiniMax H3 uses:

- **24 fps** for the target video timeline.
- **40 Hz** for the target audio-latent timeline.

Every `start_frame` is converted to a waveform sample with deterministic integer arithmetic. Each full-lock source is resampled to the H3 audio VAE's sample rate before placement.

The target waveform length is derived from the actual H3 target audio latent. Empty regions are real waveform-domain zero samples, so silence is encoded as silence instead of being represented by arbitrary zero-valued audio-latent vectors.

```text
Timed Audio 1 ----\
Timed Audio 2 -----\
Timed Audio 3 ------> deterministic waveform mixer
...                /            |
                            one exact waveform
                                  |
                             H3 Audio VAE
                                  |
                         target audio latent
                                  |
                    audio mask = 0 / video mask = 1
                                  |
                         MiniMax H3 sampling
```

## Multi-speaker lip sync

MiniMax H3 has one target-audio stream, not separate hidden audio channels for each visible speaker.

For multi-speaker scenes, use one Timed Audio event per utterance and identify the active speaker in the MiniMax H3 prompt. `MiniMaxH3AddGuide` can reinforce the same event at the same target frame.

The audio lock guarantees the supplied waveform and timing. Speaker ownership still needs to be expressed in the H3 conditioning/prompt.

## Scene-aware recursive dialogue

For recursive H3 Director / Context Loop workflows:

- `MiniMax H3 Scene Timed Audio` assigns approved `AUDIO` to a one-based scene and scene-local frame.
- `MiniMax H3 Scene Exact Audio Lock` locks the current scene's complete scheduled audio.
- `MiniMax H3 Scene Dialogue Audio Lock` protects only supplied dialogue while leaving unsupplied scene sound generative.

Connect `MiniMax H3 Chain Current.clip_index` to the scene lock's `current_scene`.

> [!IMPORTANT]
> Do not enable H3 Context Loop's complete `lock_source_audio` / `source_audio_target=locked` target lock on the same sampler path as `MiniMax H3 Scene Exact Audio Lock`. Use one owner for the complete H3 target-audio latent.

See [docs/SCENE_DIALOGUE.md](docs/SCENE_DIALOGUE.md) and [docs/DIALOGUE_PARTIAL_LOCK.md](docs/DIALOGUE_PARTIAL_LOCK.md).

## Technical notes

### Source channel handling

- Mono input is duplicated to stereo for ExactAudioLock mixing.
- Stereo input is preserved.
- More than two channels are deterministically downmixed to mono and duplicated to stereo for ExactAudioLock mixing.
- Review-gate browser previews may downmix more-than-stereo candidates to stereo for playback only; the selected workflow `AUDIO` is not replaced by the preview.

### Deterministic placement

Connected Timed Audio entries are sorted into a stable order before mixing. Placement is calculated from target frames rather than independently rounded floating-point timestamps.

### Clipping

`sum` intentionally does not normalize the result.

`prevent_clipping` changes gain only when necessary and applies the same scale to the complete finished bus.

### Audio locking

A complete ExactAudioLock gives the H3 audio target a zero-valued denoise mask while video remains denoisable.

Dialogue-only locks protect only supplied dialogue regions and configured margins while preserving generative gaps and any existing upstream protection.

## Documentation

- [Installation and Python dependencies](docs/INSTALLATION.md)
- [Audio Review / Accept Gate](docs/AUDIO_REVIEW_GATE.md)
- [Loadable example workflows](docs/EXAMPLE_WORKFLOWS.md)
- [Scene-aware recursive dialogue](docs/SCENE_DIALOGUE.md)
- [Dialogue-only / partial audio lock](docs/DIALOGUE_PARTIAL_LOCK.md)
- [Protocol and compatibility contracts](PROTOCOL.md)
- [Development and testing](DEVELOPMENT.md)

## Troubleshooting

### `ModuleNotFoundError: No module named 'av'`

Install the repository requirements with the same Python interpreter that runs ComfyUI:

```bash
python -m pip install -r requirements.txt
```

If ComfyUI uses a dedicated venv or portable interpreter, call that interpreter directly instead of the system `python`.

### `ModuleNotFoundError: No module named 'torchaudio'`

Do not blindly install a random TorchAudio wheel into the system Python. Confirm that you are running the command with the same Python environment that starts ComfyUI. Torch and TorchAudio are owned by the ComfyUI environment and may be backend-specific.

### The node does not appear

Restart ComfyUI and check the ComfyUI console for an import error from `ComfyUI-H3-ExactAudioLock`.

### A source extends past the end of the clip

With `overflow_policy=error`, this is intentional. Move the event earlier, increase the H3 target duration, shorten the source, or deliberately select `crop`.

### Overlapping dialogue throws an error

Set `mix_policy` to `sum` or `prevent_clipping`. `reject_overlap` is the strict no-overlap mode.

### The combined waveform clips

Use `prevent_clipping`, lower one or more Timed Audio `gain_db` values, or manage source levels before the lock node.

### Lip sync follows the wrong visible speaker

The locked waveform tells H3 what audio exists and when, but the single target audio stream does not independently identify visible speakers. Use clear speaker ownership in the H3 prompt and optionally reinforce each utterance with `MiniMaxH3AddGuide` at the same frame.

## Repository structure

```text
ComfyUI-H3-ExactAudioLock/
├── docs/
│   ├── AUDIO_REVIEW_GATE.md
│   ├── DIALOGUE_PARTIAL_LOCK.md
│   ├── EXAMPLE_WORKFLOWS.md
│   ├── INSTALLATION.md
│   └── SCENE_DIALOGUE.md
├── example_workflows/
│   ├── context_loop/
│   ├── qwen3_tts/
│   └── standalone/
├── tests/
│   ├── test_audio_review_gate.py
│   ├── test_example_workflows.py
│   └── test_exact_audio_lock.py
├── web/
│   └── audio_review_gate.js
├── .gitignore
├── DEVELOPMENT.md
├── LICENSE
├── PROTOCOL.md
├── README.md
├── __init__.py              # ExactAudioLock nodes and ComfyUI extension entrypoint
├── audio_review_gate.py     # Audio Review / Accept Gate backend
└── requirements.txt
```

## Acknowledgements

Development was informed by public MiniMax H3 and ComfyUI research and by community experiments around fixed target-audio conditioning. In particular, the project studied ComfyUI's native MiniMax H3 AV-latent/masking behavior, the public `MiniMaxH3NativeAudioLock` workflow by Shrek3OnVH5, and the MIT-licensed H3 Audio Sync work in ComfyUI-Pixaroma.

Those projects are technical references. ComfyUI-H3-ExactAudioLock is maintained as its own implementation and repository.

## Author and attribution

**Alan Guice (Badgids)** is the original Author/Developer of ComfyUI-H3-ExactAudioLock.

Copyright © 2026 Alan Guice (Badgids).

If you redistribute or substantially reuse the software, preserve the copyright and MIT license notice as required by the license.

## License

ComfyUI-H3-ExactAudioLock is released under the **MIT License**. See [LICENSE](LICENSE) for the full license text.

Copyright © 2026 Alan Guice (Badgids).
