# Protocol and Compatibility Contract

This document records the public contracts that saved ComfyUI workflows and
recursive H3 integrations may rely on.

## Upstream protocol references reviewed for the scene-audio milestone

The milestone was reviewed against:

- ComfyUI native MiniMax H3 implementation, including its joint nested latent
  shape, 24 fps target-video timeline, 40 Hz audio-latent timeline, and
  `MiniMaxH3AddGuide` behavior.
- ComfyUI V3 `comfy_api.latest` node schema and native `Autogrow` usage.
- `ethanfel/ComfyUI-MiniMaxH3-Context-Loop` current 0.6 contracts, especially
  one-based `clip_index` and `source_audio_target = locked`.

ExactAudioLock intentionally does not import that custom node pack. Compatibility
is by ordinary ComfyUI values and documented behavior.

## H3 AV latent contract

`MiniMax H3 Exact Audio Lock` and `MiniMax H3 Scene Exact Audio Lock` require a
joint nested H3 latent containing exactly two streams:

```text
video: [1, 24, T, H, W]
audio: [1, 32, 2, T40]
```

The package supports batch size 1. A different stream count, channel layout, or
batch size is rejected instead of being guessed.

The output preserves the video latent and replaces the target audio latent with
the one Audio-VAE encoding of the deterministic mixed waveform.

The output denoise mask is:

```text
video mask = 1  # video remains denoisable
audio mask = 0  # exact target audio is protected
```

For complete locks, `exact_audio` is the authoritative final soundtrack waveform.
A workflow that requires exact source timing must mux/assemble `exact_audio` with the
decoded video instead of replacing it with a second `VAEDecodeAudio` result from the
sampled AV latent.

## Timed Audio contract

`MiniMaxH3TimedAudio` values contain:

```text
audio       normal ComfyUI AUDIO
start_frame zero-based frame within the current H3 target at 24 fps
gain_db     finite pre-mix gain
label       diagnostic label
```

The node also outputs the validated `start_frame` as an ordinary `INT`. Standalone
dialogue workflows may connect that output directly to native
`Add Guide for MiniMax H3.frame_idx`, giving conditioning and exact-lock placement
one shared frame value.

## Scene Timed Audio contract

`MiniMaxH3SceneTimedAudio` adds:

```text
scene_index one-based scene number
```

`start_frame` remains scene-local and zero-based. It is not a global movie frame.

`MiniMaxH3SceneExactAudioLock.current_scene` is also one-based. The intended H3
Context Loop connection is:

```text
MiniMax H3 Chain Current.clip_index
        -> MiniMax H3 Scene Exact Audio Lock.current_scene
```

Only events with `scene_index == current_scene` are passed to the existing exact
mixer/lock implementation. Nonmatching events are not copied, resampled, mixed,
or encoded during that scene.

## Empty scenes

`empty_scene_policy` is explicit:

- `error` (default): fail when the current scene has no scheduled event. This
  catches missing wiring and wrong scene numbers.
- `lock_silence`: deliberately encode waveform-domain digital silence and lock
  it as the H3 target audio for that scene.

There is intentionally no implicit pass-through mode because the `exact_audio`
output would no longer describe the audio H3 actually generated.

## Autogrow execution contract

Both lock nodes accept ComfyUI V3 Autogrow runtime values in either form:

```text
normalized mapping:
    scene_timed_audios = {"scene_timed_audio_0": ...}

flattened runtime keys:
    scene_timed_audios.scene_timed_audio_0 = ...
```

Supplying conflicting normalized and flattened values for the same socket is an
error. Unexpected runtime keys are an error.

## Lock ownership

A complete target-audio lock is exclusive for a sampler invocation.

When `MiniMax H3 Scene Exact Audio Lock` is used with H3 Context Loop, leave
Context Loop's own `lock_source_audio` / `source_audio_target=locked` behavior
disabled for that same generation path. Enabling both describes two owners of
the same target-audio latent and is unsupported.

Reference audio conditioning and `MiniMaxH3AddGuide` are different mechanisms.
They may be used deliberately, but the prompt still needs to identify which
visible character owns each spoken line because H3 has one target audio stream,
not per-speaker hidden audio channels.

## Voice consistency

This package does not synthesize or clone voices. Persistent character voices
must be rendered upstream by the chosen TTS/voice-cloning workflow. Feed those
approved `AUDIO` results into Scene Timed Audio. Rerolling H3 video then reuses
the same exact waveform rather than regenerating the performance.

## Resource and security behavior

- No arbitrary path input.
- No file deletion or mutation.
- No network access.
- No subprocesses or background threads.
- No persistent cache.
- No developer-specific paths.
- Audio tensors remain referenced only for the duration of normal ComfyUI graph
  execution; Python/ComfyUI owns their lifecycle.

## Partial dialogue-lock contract

`MiniMaxH3DialogueAudioLock` and `MiniMaxH3SceneDialogueAudioLock` are partial
locks. They do **not** own the complete scene soundtrack.

Their audio mask uses native ComfyUI sampling semantics:

```text
1.0 = H3 may denoise/generate this audio-latent region
0.0 = preserve the supplied dialogue target exactly at sampling time
0..1 = feathered transition
```

The supplied dialogue is mixed and encoded once. Only the dialogue core, configured
hard margins, and optional feather regions are blended into the incoming target audio
latent. Fully generative regions retain the incoming H3 audio target. Existing
upstream video masks are preserved. Existing upstream audio masks are combined with
the dialogue mask using the more restrictive value.

The partial nodes output `dialogue_reference_audio`, the deterministic full-length
supplied-dialogue reference bus with digital silence outside supplied events, plus
`dialogue_lock_manifest`, which records every exact waveform interval.

Raw post-sampling H3 audio is not the final timing authority for supplied dialogue.
`MiniMax H3 Dialogue Audio Finalize` takes the decoded generated audio, the matching
`dialogue_reference_audio`, and the matching `dialogue_lock_manifest`. For every
track with mixed samples it replaces exactly `[start_sample,end_sample)` with the
deterministic reference samples and preserves H3-generated samples outside those
core intervals. This makes the supplied voice onset waveform-sample authoritative
while retaining generated ambience/effects elsewhere.

The scene-aware partial node defaults to `empty_scene_policy = generate`. A scene
without scheduled dialogue is a true pass-through for the latent and remains fully
available to H3, subject to any mask already present upstream. `error` is available
when a production wants missing dialogue schedules to fail closed.

Default protection values are:

```text
protect_before_ms = 200
protect_after_ms  = 250
feather_ms        = 100
```

All three values are explicit workflow parameters and are validated in the range
0..5000 ms. H3's audio latent is 40 Hz, so millisecond safety intervals are rounded
up to the latent grid. The event core is conservatively mapped from waveform samples
to the latent grid so no supplied dialogue sample is intentionally left outside the
hard-protected core.

Complete target-audio locks remain exclusive owners of the full audio target. Partial
dialogue locks may coexist with upstream continuation masks because they preserve and
combine those masks rather than replacing them.
## Audio Review / Accept Gate contract

`H3ExactAudioLockAudioReviewGate` provides Context-Loop-style candidate review
without importing H3 Context Loop or any TTS/music implementation. It consumes
ordinary ComfyUI values and therefore works with any producer that exposes standard
`AUDIO`.

Two source modes are public:

```text
connected_audio:
    audio + Autogrow audio_candidates -> candidate set

audio_file:
    audio_file + Autogrow audio_files -> candidate set
```

Connected `AUDIO` with waveform batch size greater than one is split into individual
review candidates. Direct file candidates are managed ComfyUI input selections only;
supported extensions are `.wav`, `.mp3`, `.flac`, `.ogg`, `.oga`, and `.opus`.

For one pending review, the browser may navigate all candidates, select one candidate
for acceptance, and mark zero or more other candidates as kept alternates. The public
decision contract is:

```text
selected candidate -> accepted_audio output only
kept candidates     -> durable alternate files only
other candidates    -> rejected/discarded
```

The selected candidate is never concatenated with another candidate. Resolving one
review removes that review's backend pending state and frontend candidate state, so a
later loop/requeue review starts only with its own candidates and cannot emit prior
accepted takes.

Marked connected alternates are written as float WAV below
`output/h3_exact_audio_lock_review/alternates/<review-token>/`. Marked direct-file
alternates are copied there while preserving their original supported encoded format.
The selected candidate is not duplicated as an alternate.

Temporary candidate previews exist only under the gate-owned ComfyUI temp subtree.
Preview transforms are UI-only and never replace the selected workflow `AUDIO`.
More-than-stereo audio may be downmixed only for its disposable browser preview.

`delete_rejected_audio_files` is source-aware. Connected `AUDIO` never causes an
upstream file deletion. In `audio_file` mode, after an explicit decision and only
when deletion is enabled, the gate may delete exact unselected/unkept managed input
files after revalidating confinement. Selected and kept files are retained. Timeouts,
decode failures, notification failures, and alternate-save failures do not delete
source files. Gate-owned previews are cleaned after accept, reject, timeout, or
failure.

The frontend routes review state to the matching embedded node panel using ComfyUI's
node `unique_id`. Review communication uses ComfyUI's same-origin server/websocket
infrastructure. The gate performs no external network access and starts no subprocess
or background worker thread.
