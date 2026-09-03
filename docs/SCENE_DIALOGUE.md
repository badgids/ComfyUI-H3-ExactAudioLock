# Scene-aware dialogue for recursive H3 workflows

The scene-aware nodes let one recursive H3 sampling body reuse a complete set of
approved spoken lines without leaking scene 1's dialogue into scene 2.

## Nodes

### MiniMax H3 Scene Timed Audio

Create one node for each approved line or other audio event.

Set:

- `scene_index`: one-based scene number.
- `start_frame`: scene-local 24 fps frame where the event begins.
- `gain_db`: optional pre-mix gain.
- `label`: speaker/event name for diagnostics.

The `AUDIO` input can come from Qwen3-TTS, another TTS/voice clone, a normal
audio loader, or any ComfyUI node that returns `AUDIO`.

### MiniMax H3 Scene Exact Audio Lock

Connect:

```text
Scheduled Ref2VA / H3 joint AV latent
        -> av_latent

MiniMax H3 audio VAE
        -> audio_vae

MiniMax H3 Chain Current.clip_index
        -> current_scene

all MiniMax H3 Scene Timed Audio nodes
        -> scene_timed_audios (Autogrow)

locked_av_latent
        -> H3 sampler latent input
```

Only the current scene's events are mixed. Other scene events remain inactive for
that recursive iteration.

## Recommended dialogue workflow

```text
persistent character voice / voice clone
        -> render and approve each spoken line once
        -> Scene Timed Audio (scene + local frame)
        -> Scene Exact Audio Lock
        -> H3 sampler
        -> draft review
```

Changing the video seed, camera, prompt, or upscale pass does not change the
approved waveform. If a performance changes, rerender that TTS line deliberately
and update the corresponding Scene Timed Audio input.

## Empty scenes

Keep `empty_scene_policy=error` while building the schedule. It catches missing
scene assignments immediately.

Choose `lock_silence` only for a scene that intentionally has no target sound.
If the film needs room tone, ambience, music, or effects, schedule those AUDIO
events rather than using silence.

## Multi-speaker scenes

Multiple lines can share a scene and can overlap. `mix_policy=sum` preserves the
exact arithmetic mix. `prevent_clipping` applies one deterministic global scale
only when needed. `reject_overlap` is available for strict dialogue layouts.

ExactAudioLock controls the one H3 target audio stream. Put speaker ownership in
the H3 prompt, for example by stating who speaks each line and that other visible
characters keep their mouths closed.

`MiniMaxH3AddGuide` can still reinforce an utterance, but it is a conditioning
mechanism and must be scheduled consistently with the same scene/frame. This
milestone does not add a recursive AddGuide scheduler.

## H3 Context Loop audio policy

Do not enable Context Loop's complete `lock_source_audio` target lock on the same
sampler path when Scene Exact Audio Lock is active. Pick one owner for the target
audio latent.

For a Director workflow using this node pack, use the Scene Exact Audio Lock as
the target owner and keep Context Loop's source target lock off.

## Dialogue-only lock for H3-generated soundscapes

When supplied speech must remain stable but H3 should generate room tone, ambience,
music, Foley, footsteps, impacts, explosions, and other scene audio, use `MiniMax H3
Scene Dialogue Audio Lock` instead of `MiniMax H3 Scene Exact Audio Lock`.

The scene dialogue node uses the same Scene Timed Audio events and one-based
`current_scene` routing, but protects only dialogue intervals and configured safety
margins. Its `dialogue_reference_audio` output is a reference stem, not the final
soundtrack. Use H3's decoded post-sampling audio for the final scene mix.

See `docs/DIALOGUE_PARTIAL_LOCK.md` for mask semantics, defaults, and continuation
behavior.
