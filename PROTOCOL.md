# Protocol and Compatibility Contract

This document records the public contracts that saved ComfyUI workflows and
recursive H3 integrations may rely on.

## Upstream protocol references reviewed for the scene-audio milestone

The milestone was reviewed against:

- ComfyUI native MiniMax H3 implementation, including its joint nested latent
  shape, 24 fps target-video timeline, 40 Hz audio-latent timeline, and
  `MiniMaxH3AddGuide` behavior.
- ComfyUI V3 `comfy_api.latest` node schema and native `Autogrow` usage.
- `ethanfel/ComfyUI-MiniMaxH3-Context-Loop` current 0.5 contracts, especially
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

## Timed Audio contract

`MiniMaxH3TimedAudio` values contain:

```text
audio       normal ComfyUI AUDIO
start_frame zero-based frame within the current H3 target at 24 fps
gain_db     finite pre-mix gain
label       diagnostic label
```

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

The partial nodes output `dialogue_reference_audio`, which is the deterministic
supplied-dialogue stem with digital silence outside supplied events. It is **not**
the final soundtrack and must not be used to replace H3's post-sampling audio when
room tone, ambience, music, effects, footsteps, or other generated audio is desired.

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
