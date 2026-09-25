# Scene-aware dialogue for recursive H3 workflows

The scene-aware nodes let one recursive H3 sampling body reuse a complete set of approved spoken lines without leaking one scene's dialogue into another.

## Nodes

### MiniMax H3 Scene Timed Audio

Create one node for each approved line or other scene audio event.

Set:

- `scene_index`: one-based scene number.
- `start_frame`: scene-local 24 fps frame where the event begins.
- `gain_db`: optional pre-mix gain.
- `label`: speaker/event name for diagnostics.

The node also exposes the validated scene-local `start_frame` as an `INT` output.

The `AUDIO` input can come from Qwen3-TTS, another TTS/voice clone, a normal audio loader, or any ComfyUI node that returns `AUDIO`.

## Scene full lock

`MiniMax H3 Scene Exact Audio Lock` selects only the events for `current_scene`, delegates them to the normal full Exact Audio Lock, and returns that scene's deterministic `exact_audio`.

```text
MiniMax H3 Chain Current.clip_index ─────────► current_scene
all Scene Timed Audio events ────────────────► scene_timed_audios
MiniMax H3 Chain Context.latent ─────────────► av_latent
MiniMax H3 audio VAE ────────────────────────► audio_vae

                                  MiniMax H3 Scene Exact Audio Lock
                                             │
                   ┌─────────────────────────┴─────────────────────────┐
                   ▼                                                   ▼
            locked_av_latent                                      exact_audio
                   │                                                   │
                   ▼                                                   │
        SamplerCustomAdvanced                                         │
                   │                                                   │
                   ▼                                                   │
              VAEDecode ───────────────────────────────────────────────┤
                                                                       ▼
                                                         MiniMax H3 Loop Trim.audio
```

For a full scene lock, use `exact_audio` in the segment/trim/final mux path. Do not substitute sampler-decoded H3 audio for the deterministic scene master.

Only the current scene's events are mixed. Other scene events remain inactive for that recursive iteration.

## Scene dialogue-partial lock

Use `MiniMax H3 Scene Dialogue Audio Lock` when supplied speech must remain exact but H3 should generate room tone, ambience, music, Foley, footsteps, impacts, explosions, and other unsupplied scene audio.

```text
MiniMax H3 Chain Current.clip_index ─────────► Scene Dialogue Audio Lock.current_scene
Scene Timed Audio events ────────────────────► Scene Dialogue Audio Lock.scene_timed_audios
MiniMax H3 Chain Context.latent ─────────────► Scene Dialogue Audio Lock.av_latent

Scene Dialogue Audio Lock.dialogue_locked_av_latent ─► SamplerCustomAdvanced
                                                            │
                                                            ▼
                                                     VAEDecodeAudio
                                                            │ generated_audio
                                                            ▼
                                             MiniMax H3 Dialogue Audio Finalize
Scene Dialogue Audio Lock.dialogue_reference_audio ─────────►
Scene Dialogue Audio Lock.dialogue_lock_manifest ───────────►
                                                            │
                                                            ▼
                                                      final_audio
                                                            │
                                                            ▼
                                                 MiniMax H3 Loop Trim.audio
```

The voice start frame remains authoritative in partial mode. H3 generative freedom applies only outside the supplied dialogue cores. The finalizer restores those cores sample-for-sample after H3 audio decode.

## Recommended production workflow

```text
persistent character voice / voice clone
        -> render and approve each spoken line once
        -> Scene Timed Audio (scene + local frame)
        -> Scene Exact Audio Lock OR Scene Dialogue Audio Lock
        -> H3 sampler
        -> deterministic final audio path
        -> review
```

Changing the video seed, camera, prompt, or upscale pass does not change the approved waveform. If a performance changes, rerender that TTS line deliberately and update the corresponding Scene Timed Audio input.

## Empty scenes

For `MiniMax H3 Scene Exact Audio Lock`, keep `empty_scene_policy=error` while building a strict complete schedule. It catches missing scene assignments immediately. Use `lock_silence` only when a scene intentionally has no target sound.

For `MiniMax H3 Scene Dialogue Audio Lock`, `empty_scene_policy=generate` is normally correct. Scenes with no supplied dialogue retain H3's generated audio.

## Multi-speaker scenes

Multiple lines can share a scene and can overlap. `mix_policy=sum` preserves the exact arithmetic mix. `prevent_clipping` applies one deterministic global scale only when needed. `reject_overlap` is available for strict dialogue layouts.

ExactAudioLock controls one H3 target-audio stream. Put speaker ownership in the H3 prompt, for example by stating who speaks each line and that other visible characters keep their mouths closed.

## Native Add Guide conditioning

ComfyUI's `Add Guide for MiniMax H3` (`MiniMaxH3AddGuide`) can reinforce an utterance at the same video frame. In a static standalone graph, wire Timed Audio's `start_frame` output directly to `frame_idx` so the values cannot diverge.

A recursive Context Loop graph needs current-scene-aware routing before a static Add Guide can safely select one scene's event. The scene lock/finalizer does not depend on Add Guide for final soundtrack placement; its timing comes from the selected Scene Timed Audio event and manifest.

## H3 Context Loop audio policy

Do not enable Context Loop's complete `lock_source_audio` target lock on the same sampler path when `MiniMax H3 Scene Exact Audio Lock` owns the target audio. Pick one owner for the complete audio target.

With scene dialogue-partial locking, use the scene dialogue lock before sampling and `MiniMax H3 Dialogue Audio Finalize` after H3 audio decode.

See `docs/DIALOGUE_PARTIAL_LOCK.md` for mask semantics, exact finalization, defaults, and continuation behavior.
## Production-wide Dialogue Timeline path

Large productions do not need one review gate and one Scene Timed Audio node per
line. The batch path is:

```text
Context Loop Plan + all dialogue AUDIO
        -> MiniMax H3 Dialogue Timeline
        -> Dialogue Review / Approval Board
        -> MiniMax H3 Current Scene Dialogue
             ^ current_scene = Chain Current.clip_index
        -> Scene Dialogue Audio Lock.dialogue_event_set
```

`MiniMax H3 Dialogue Timeline` reads each scene's `<d>...</d>` tags and maps
connected AUDIO in plan order. It uses Context Loop's `raw_frames` and
`delivered_frames` to account for repeated continuation head frames before
creating an initial scene-local layout.

The Approval Board lets the user listen to every required line and edit exact
raw scene-local `start_frame` values before expensive H3 generation begins.

`MiniMax H3 Current Scene Dialogue` then filters the production-wide approved set
by the one-based `clip_index`. A scene with no events produces an empty event set;
`MiniMax H3 Scene Dialogue Audio Lock` with `empty_scene_policy=generate` leaves
that scene's audio fully generative.

The existing `scene_timed_audios` Autogrow remains supported for older workflows.
Do not intentionally feed the same event through both the event-set and legacy
Scene Timed Audio paths.

See `docs/DIALOGUE_REVIEW_BOARD.md`.
