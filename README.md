# ComfyUI-H3-ExactAudioLock

---

**Author/Developer:** [Alan Guice (Badgids)](https://github.com/badgids)  
**Copyright:** © 2026 Alan Guice (Badgids).  
**License:** [MIT License](LICENSE)

---

ComfyUI-H3-ExactAudioLock is a ComfyUI custom-node package for MiniMax H3 that builds one deterministic, sample-accurate target-audio waveform from any number of independently timed audio sources, encodes that waveform into H3's target audio latent, locks the audio stream against denoising, and leaves the video stream free to denoise against the exact supplied sound.

It is designed for dialogue, multi-speaker scenes, overlapping speech, singing, music-video work, and other MiniMax H3 generations where the picture needs to respond to exact user-supplied audio instead of regenerated or approximate audio.

> [!NOTE]
> ComfyUI-H3-ExactAudioLock is an independent community custom node. It is not an official ComfyUI or MiniMax project.

## Features

- Locks an exact user-controlled waveform into MiniMax H3's target audio latent.
- Keeps H3 video denoisable while the target audio stream remains fixed.
- Accepts an effectively unlimited number of timed audio events through ComfyUI's native `Autogrow` inputs.
- Places every source on H3's 24 fps target-video timeline using deterministic integer frame-to-sample conversion.
- Mixes multiple speakers or sound sources sample-accurately into one H3 target waveform.
- Supports overlapping dialogue and layered audio.
- Resamples every source to the MiniMax H3 audio VAE sample rate before mixing.
- Uses waveform-domain digital silence rather than zero-valued latent padding.
- Provides per-source gain control and optional speaker/event labels.
- Provides deterministic overlap and overflow policies.
- Returns the exact mixed `AUDIO` used for H3 plus a JSON mix manifest for diagnostics and reproducibility.
- Preserves the original single-audio input as a backward-compatible path for older workflows.
- Can be used together with ComfyUI's core `MiniMaxH3AddGuide` node for event-level audio reinforcement at the same target frame.

## Nodes

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

The lock node accepts the H3 AV latent, MiniMax H3 audio VAE, and any number of `MiniMax H3 Timed Audio` inputs. It builds one exact target waveform, encodes it once, replaces H3's target audio latent, gives audio a zero denoise mask, and gives video a one denoise mask.

| Input | Purpose |
| --- | --- |
| `av_latent` | Joint MiniMax H3 video/audio target latent. |
| `audio_vae` | MiniMax H3 audio VAE. |
| `timed_audios` | Native ComfyUI `Autogrow` input. Connect as many Timed Audio nodes as needed. |
| `mix_policy` | Controls how overlapping sources are handled. |
| `overflow_policy` | Controls what happens when a source extends beyond the H3 target timeline. |
| `audio` | Optional legacy single-audio input for older workflows. |
| `legacy_start_frame` | Target frame for the optional legacy audio input. |

Outputs:

| Output | Purpose |
| --- | --- |
| `locked_av_latent` | H3 AV latent with the exact audio target inserted and locked. |
| `exact_audio` | Exact waveform that was mixed and encoded into H3. |
| `mix_manifest` | Deterministic JSON describing source placement, gains, sample counts, crop/scale decisions, and peaks. |

## Unlimited Timed Audio Inputs

`MiniMaxH3ExactAudioLock` uses ComfyUI's native `Autogrow` socket mechanism and intentionally defines **no maximum number of timed-audio sockets**.

Connect another `MiniMax H3 Timed Audio` node and ComfyUI grows another input. The custom node does not impose an arbitrary speaker or utterance limit. Practical limits are the ComfyUI graph size and available system resources.

```text
Speaker 1 AUDIO -> MiniMax H3 Timed Audio --\
Speaker 2 AUDIO -> MiniMax H3 Timed Audio ----\
Speaker 3 AUDIO -> MiniMax H3 Timed Audio ------> MiniMax H3 Exact Audio Lock
Speaker 4 AUDIO -> MiniMax H3 Timed Audio ----/
...                                         --/
```

## Layering and Overlapping Audio

Overlapping sources are supported.

For example:

```text
24 fps target timeline

Pippa     ----[ dialogue A ]---------------------------
Magnus           ----[ dialogue B ]--------------------
Cricket                    --[ dialogue C ]------------

Mixed bus ----[ A + overlap(A,B) + overlap(B,C) ]------
```

Choose one `mix_policy`:

- `sum` — exact arithmetic summing. Overlaps are allowed and mixed sample-for-sample. This is the normal policy for layered audio.
- `prevent_clipping` — mixes overlaps normally, then applies one deterministic global scale only when the finished bus peak exceeds `0.999`.
- `reject_overlap` — treats any overlapping source intervals as an error. Use this only when overlaps are intentionally forbidden.

Choose one `overflow_policy`:

- `error` — fail if an event starts outside or extends beyond the H3 target audio timeline.
- `crop` — crop the event at the target boundary.

For dialogue production, `sum` + `error` is a useful strict default: overlapping speakers are allowed, but speech is never silently cut off at the end of the clip.

## How Timing Works

MiniMax H3 uses:

- **24 fps** for the target video timeline.
- **40 Hz** for the target audio-latent timeline.

Every `start_frame` is converted to a waveform sample with deterministic integer arithmetic. Each source is resampled to the H3 audio VAE's sample rate before placement.

The target waveform length is derived from the actual H3 target audio latent. Empty regions are real waveform-domain zero samples, so silence is encoded as silence instead of being represented by arbitrary zero-valued audio-latent vectors.

The complete waveform is mixed **before** one Audio-VAE encode pass:

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

## Multi-Speaker Lip Sync

The node supplies MiniMax H3 with one exact target-audio stream. It does not create separate hidden H3 speaker channels because H3's target AV latent contains one audio stream.

For multi-speaker scenes, use one Timed Audio event per utterance and identify the active speaker in the MiniMax H3 prompt. For stronger event-level reinforcement, the same original `AUDIO` source can also feed ComfyUI's core `MiniMaxH3AddGuide` node at the identical `start_frame`.

A typical hybrid path is:

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

## Requirements

- A current ComfyUI installation with MiniMax H3 support.
- MiniMax H3 joint AV latents.
- The MiniMax H3 audio VAE.
- A ComfyUI build with the current `comfy_api.latest` node API and `Autogrow` support.
- PyTorch and TorchAudio from the active ComfyUI Python environment.

The node does not require a separate Python virtual environment.

## Installation

### Install with Git

From the ComfyUI `custom_nodes` directory:

```bash
cd /path/to/ComfyUI/custom_nodes
git clone https://github.com/badgids/ComfyUI-H3-ExactAudioLock.git
```

Restart ComfyUI after installation.

### Install manually

Copy the repository directory to:

```text
ComfyUI/custom_nodes/ComfyUI-H3-ExactAudioLock/
```

The resulting layout should contain:

```text
ComfyUI/
└── custom_nodes/
    └── ComfyUI-H3-ExactAudioLock/
        ├── LICENSE
        ├── README.md
        └── __init__.py
```

Restart ComfyUI afterward.

## Basic Usage

### Ref2VA workflow (recommended for character and scene continuity)

ComfyUI already creates the joint H3 audio/video latent. This custom-node pack does **not** replace or duplicate ComfyUI's native H3 latent-creation nodes.

For Ref2VA, use ComfyUI's native **`MiniMax H3 Reference to Video`** node. Its `latent` output is the joint MiniMax H3 AV latent that `MiniMax H3 Exact Audio Lock` expects.

Wire the core H3 path like this:

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

1. Create the normal H3 Ref2VA workflow with ComfyUI's native `MiniMax H3 Reference to Video` node.
2. Connect its `latent` output to `MiniMax H3 Exact Audio Lock` → `av_latent`.
3. Connect the same MiniMax H3 audio VAE used by the H3 workflow to `MiniMax H3 Exact Audio Lock` → `audio_vae`.
4. Load each dialogue, vocal, music, or other audio source with a normal ComfyUI audio loader.
5. Connect each source to its own `MiniMax H3 Timed Audio` node.
6. Set each Timed Audio node's exact `start_frame` and optional `gain_db` / label.
7. Connect every Timed Audio output to the autogrowing `timed_audios` inputs on `MiniMax H3 Exact Audio Lock`. Add as many Timed Audio connections as needed.
8. Select the desired `mix_policy` and `overflow_policy`. `sum` allows normal layering and overlapping speakers.
9. Connect `locked_av_latent` to the sampler **instead of** the original unlocked H3 latent.
10. Continue the normal H3 sampling/decoding workflow.
11. Use `exact_audio` when you need the exact mixed waveform for muxing, review, or verification.
12. Inspect `mix_manifest` when diagnosing source placement, gain, overlap, or overflow behavior.

### Using `MiniMaxH3AddGuide` at the same time

`MiniMax H3 Exact Audio Lock` controls the H3 target-audio latent. `MiniMaxH3AddGuide` controls event-level conditioning. They can be used together.

For dialogue-heavy lip sync, the intended hybrid wiring is:

```text
                                   ┌───────────────────────────────┐
                                   │ MiniMax H3 Reference to Video │
                                   │          (Ref2VA)             │
                                   └───────────────┬───────────────┘
                                                   │
                          positive ────────────────┤
                                                   │ latent
                                                   ▼
                                         MiniMax H3 Exact Audio Lock
                                                   │
                                                   ▼
                                            locked_av_latent
                                                   │
                                                   ▼
                                               H3 sampler

Speaker A audio ─► MiniMax H3 Timed Audio @ frame N ─┐
Speaker B audio ─► MiniMax H3 Timed Audio @ frame M ─┼─► Exact Audio Lock timed_audios
Speaker C audio ─► MiniMax H3 Timed Audio @ frame K ─┘

The same Speaker A/B/C audio can also be connected through one or more
MiniMaxH3AddGuide nodes at the same frame indices on the positive-conditioning path.
```

The lock node always produces one exact mixed H3 target-audio stream. Multiple Timed Audio entries can overlap; with `mix_policy = sum`, their waveforms are mixed sample-for-sample on that shared target timeline.

### Other native H3 modes

If you are not using Ref2VA, connect the joint AV `LATENT` produced by the appropriate native ComfyUI MiniMax H3 node. For example, ComfyUI also provides `MiniMax H3 Image to Video` and `Empty MiniMax H3 AV Latent` for workflows that use those paths. The lock node expects an H3 joint AV latent; it does not expect a standalone image/video latent.

## Technical Notes

### Source channel handling

- Mono input is duplicated to stereo.
- Stereo input is preserved.
- More than two channels are deterministically downmixed to mono and duplicated to stereo.

### Deterministic placement

Connected Timed Audio entries are sorted into a stable order before mixing. Placement is calculated from target frames rather than independently rounded floating-point timestamps.

### Clipping

`sum` intentionally does not normalize the result. This preserves exact arithmetic layering and leaves level management to the workflow.

`prevent_clipping` changes gain only when necessary and applies the same scale to the complete finished bus.

### Audio locking

The resulting encoded audio latent receives a zero-valued denoise mask while the video latent receives a one-valued denoise mask. H3 therefore denoises the picture while attending to the fixed target audio.

## Scene-aware recursive dialogue

For recursive H3 Director / Context Loop workflows, this package now provides:

- **MiniMax H3 Scene Timed Audio** — assigns an approved `AUDIO` event to a
  one-based scene and scene-local 24 fps start frame.
- **MiniMax H3 Scene Exact Audio Lock** — accepts all scheduled scene events,
  selects only the current scene, and delegates to the same deterministic exact
  mixer/target lock used by the original node.

Connect `MiniMax H3 Chain Current.clip_index` to the scene lock's
`current_scene`. The integration uses only normal ComfyUI values; this package
does not import or require H3 Context Loop.

For persistent character voices, render/approve each line upstream with the TTS
or voice-cloning workflow of your choice and feed that stable `AUDIO` into Scene
Timed Audio. ExactAudioLock preserves the supplied waveform; it does not create
or clone the voice itself.

See [docs/SCENE_DIALOGUE.md](docs/SCENE_DIALOGUE.md) for wiring and production
guidance. See [PROTOCOL.md](PROTOCOL.md) for compatibility rules.

> [!IMPORTANT]
> Do not enable H3 Context Loop's complete `lock_source_audio` /
> `source_audio_target=locked` target lock on the same sampler path. Use one
> owner for the complete H3 target-audio latent.

## Troubleshooting

### The node does not appear

Restart ComfyUI and check the ComfyUI console for an import error from `ComfyUI-H3-ExactAudioLock`.

### A source extends past the end of the clip

With `overflow_policy = error`, this is intentional. Move the event earlier, increase the H3 target duration, shorten the source, or deliberately select `crop`.

### Overlapping dialogue throws an error

Set `mix_policy` to `sum` or `prevent_clipping`. `reject_overlap` is the strict no-overlap mode.

### The combined waveform clips

Use `prevent_clipping`, lower one or more Timed Audio `gain_db` values, or manage source levels before the lock node.

### Lip sync follows the wrong visible speaker

The locked waveform tells H3 exactly what audio exists and when, but the single target audio stream does not independently identify visible speakers. Use clear speaker ownership in the H3 prompt and optionally reinforce each utterance with `MiniMaxH3AddGuide` at the same frame.

## Repository Structure

```text
ComfyUI-H3-ExactAudioLock/
├── docs/
│   └── SCENE_DIALOGUE.md
├── tests/
│   └── test_exact_audio_lock.py
├── DEVELOPMENT.md
├── LICENSE
├── PROTOCOL.md
├── README.md
└── __init__.py
```

## Acknowledgements

Development was informed by public MiniMax H3 and ComfyUI research and by community experiments around fixed target-audio conditioning. In particular, the project studied ComfyUI's native MiniMax H3 AV-latent/masking behavior, the public `MiniMaxH3NativeAudioLock` workflow by Shrek3OnVH5, and the MIT-licensed H3 Audio Sync work in ComfyUI-Pixaroma.

Those projects are technical references. ComfyUI-H3-ExactAudioLock is maintained as its own implementation and repository.

## Author and Attribution

**Alan Guice (Badgids)** is the original Author/Developer of ComfyUI-H3-ExactAudioLock.

Copyright © 2026 Alan Guice (Badgids).

If you redistribute or substantially reuse the software, preserve the copyright and MIT license notice as required by the license.

## License

ComfyUI-H3-ExactAudioLock is released under the **MIT License**. See [LICENSE](LICENSE) for the full license text.

Copyright © 2026 Alan Guice (Badgids).
