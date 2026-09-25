# Dialogue-Only / Partial Audio Lock

Use this mode when character dialogue must remain deterministic and begin on the exact scheduled H3 video frame while MiniMax H3 still generates the rest of the scene soundtrack.

## Nodes

- `MiniMax H3 Timed Audio`: stores one approved `AUDIO` event and its exact 24 fps `start_frame`; its `start_frame` output can drive native `Add Guide for MiniMax H3.frame_idx`.
- `MiniMax H3 Dialogue Audio Lock`: non-recursive timed-dialogue partial lock.
- `MiniMax H3 Scene Timed Audio`: scene-aware event wrapper; it also exposes the validated scene-local `start_frame`.
- `MiniMax H3 Scene Dialogue Audio Lock`: recursive/Context Loop variant using one-based `current_scene`.
- `MiniMax H3 Dialogue Audio Finalize`: post-sampling finalizer that restores the supplied dialogue at the exact scheduled waveform samples while preserving H3-generated audio everywhere else.

## Exact timing contract

Partial lock does **not** mean approximate dialogue timing. It means only the unsupplied parts of the soundtrack remain generative.

For every supplied utterance:

1. `start_frame` is interpreted on H3's 24 fps target-video timeline.
2. ExactAudioLock converts that frame to the nearest deterministic waveform sample using integer arithmetic.
3. The full target-length dialogue reference contains digital silence before that sample.
4. The supplied dialogue latent is protected at the corresponding H3 audio-latent interval during sampling.
5. After H3 generates the unsupplied soundtrack, `MiniMax H3 Dialogue Audio Finalize` restores the supplied waveform sample-for-sample inside the scheduled dialogue core.

The finalizer is what makes the final exported soundtrack authoritative at the exact requested sample boundary instead of relying on a second Audio-VAE decode to reproduce the original waveform perfectly.

## Standalone wiring

For visible dialogue, use the same frame for lock placement and native H3 guide conditioning. The safest wiring uses the Timed Audio node's `start_frame` output as the one source of truth:

```text
Approved AUDIO ───────────────┬────────────► MiniMax H3 Timed Audio
                              │                       │
                              │                       ├── timed_audio ─────────┐
                              │                       │                        │
                              │                       └── start_frame ──┐      │
                              │                                         │      │
                              └────────────► Add Guide for MiniMax H3  │      │
H3 positive ────────────────────────────────► positive                  │      │
H3 AV latent ───────────────────────────────► latent                    │      │
H3 audio VAE ───────────────────────────────► audio_vae                 │      │
Timed Audio.start_frame ────────────────────► frame_idx ◄───────────────┘      │
                                                                            ▼
H3 AV latent ─────────────────────────────────────────► MiniMax H3 Dialogue Audio Lock
H3 audio VAE ─────────────────────────────────────────►           │
                                                                  ├── dialogue_locked_av_latent ─► H3 sampler
                                                                  ├── dialogue_reference_audio ───────────┐
                                                                  └── dialogue_lock_manifest ─────────────┤
                                                                                                          │
H3 sampler ─► VAEDecodeAudio ─► generated_audio ─► MiniMax H3 Dialogue Audio Finalize                    │
                                                      ▲                                                   │
                                                      └───────────────────────────────────────────────────┘
                                                                          │
                                                                          ▼
                                                                      final_audio
                                                                          │
                                                                          ▼
                                                                 Create Video / mux
```

`Add Guide for MiniMax H3` is ComfyUI's native display label for `MiniMaxH3AddGuide`. It improves event-level H3 conditioning; the lock/finalizer remain the soundtrack timing authority.

## Context Loop / Director wiring

```text
MiniMax H3 Chain Current.clip_index
        └──────────────────────────────► Scene Dialogue Audio Lock.current_scene

Scene Timed Audio events
        └──────────────────────────────► Scene Dialogue Audio Lock.scene_timed_audios

MiniMax H3 Chain Context.latent
        └──────────────────────────────► Scene Dialogue Audio Lock.av_latent

Scene Dialogue Audio Lock.dialogue_locked_av_latent
        └──────────────────────────────► SamplerCustomAdvanced.latent_image

Sampler output ─► VAEDecodeAudio ────────────────► Dialogue Audio Finalize.generated_audio
Scene Dialogue Audio Lock.dialogue_reference_audio ─► Dialogue Audio Finalize.dialogue_reference_audio
Scene Dialogue Audio Lock.dialogue_lock_manifest ───► Dialogue Audio Finalize.dialogue_lock_manifest

Dialogue Audio Finalize.final_audio
        └──────────────────────────────► MiniMax H3 Loop Trim.audio
```

Apply the scene lock after Context Loop has established its continuation/context latent and before sampling. Run the finalizer after H3 audio decode and before the segment/trim audio path.

## What is protected during sampling

Each scheduled dialogue event contributes:

1. its exact mixed waveform interval;
2. a hard-protected margin before the line;
3. a hard-protected margin after the line; and
4. an optional feather outside those hard margins.

The hard dialogue region has an audio denoise mask of zero. Outside the feather the mask is one, so H3 can generate room tone, ambience, music, footsteps, explosions, cloth movement, environmental sounds, and other prompted audio.

The finalizer replaces only the exact dialogue core intervals from the manifest. H3-generated samples outside those cores are preserved.

## Output roles

`dialogue_reference_audio` is a deterministic full-length reference bus. Silence outside supplied dialogue is intentional.

Do not mux `dialogue_reference_audio` directly as the complete soundtrack if you want H3-generated ambience/effects. Do not treat raw `VAEDecodeAudio` as the final dialogue authority either.

The final partial-lock soundtrack is:

```text
VAEDecodeAudio + dialogue_reference_audio + dialogue_lock_manifest
        -> MiniMax H3 Dialogue Audio Finalize
        -> final_audio
```

## Empty scenes

For film production, `empty_scene_policy = generate` is usually correct. Scenes with no dialogue pass through unchanged and H3 generates audio normally. The finalizer sees a zero-track manifest and leaves generated audio unchanged except for normal length/sample-rate alignment.

Use `error` only when every scene is expected to contain scheduled dialogue and a missing line should stop the render.

## Continuation safety

The dialogue lock preserves any upstream video denoise mask and combines its audio mask with existing upstream audio protection. Fully generative regions retain the incoming H3 audio latent; they are not replaced by an encoded-silence latent.

This matters for recursive Context Loop workflows, where the incoming target may already contain protected continuation material.

## Suggested starting values

```text
protect_before_ms  = 200
protect_after_ms   = 250
feather_ms         = 100
mix_policy         = prevent_clipping
overflow_policy    = error
empty_scene_policy = generate
```

Adjust margins if a voice has audible breaths or consonant tails that extend outside the nominal event boundary. The event's actual start still comes from `start_frame`; margins do not move the supplied waveform.

## Acceptance criteria

This implementation is accepted only when all of the following remain true:

- a nonzero Timed Audio `start_frame` produces silence before the exact scheduled waveform sample;
- the dialogue reference bus begins the supplied voice at that same scheduled sample;
- the protected latent core begins at the corresponding H3 audio-latent interval;
- `MiniMax H3 Dialogue Audio Finalize` restores the supplied dialogue samples at the manifest's exact core interval;
- generated H3 audio outside supplied dialogue cores is preserved;
- scene variants preserve scene-local frame placement when selecting the current scene;
- full-lock node IDs, inputs, and existing output indices remain compatible;
- existing upstream audio protection is never weakened;
- scenes without dialogue can pass through without invoking the Audio VAE;
- normalized and flattened ComfyUI Autogrow forms remain supported;
- malformed H3 latents, masks, manifests, scene indices, margins, and non-finite values fail loudly before producing misleading output.
## Batch-approved dialogue event sets

`MiniMax H3 Scene Dialogue Audio Lock` also accepts an optional
`dialogue_event_set` from the production-wide dialogue workflow:

```text
Dialogue Timeline
  -> Dialogue Review / Approval Board
  -> Current Scene Dialogue
  -> Scene Dialogue Audio Lock.dialogue_event_set
```

Every event in that set has already been approved and carries its one-based
`scene_index`, exact raw scene-local `start_frame`, gain, label, and AUDIO.
The lock consumes only the events selected for the current Context Loop scene.

This does not change finalization semantics: `MiniMax H3 Dialogue Audio Finalize`
still restores the deterministic reference samples inside the manifest's exact
dialogue intervals after sampling.

See `docs/DIALOGUE_REVIEW_BOARD.md`.
