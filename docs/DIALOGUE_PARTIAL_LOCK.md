# Dialogue-Only / Partial Audio Lock

Use this mode when character dialogue must remain deterministic but MiniMax H3 should
still generate the rest of the scene soundtrack.

## Nodes

- `MiniMax H3 Dialogue Audio Lock`: non-recursive timed-dialogue partial lock.
- `MiniMax H3 Scene Dialogue Audio Lock`: recursive/Director version using one-based
  `current_scene` and `MiniMax H3 Scene Timed Audio` events.

## Director wiring

```text
MiniMax H3 Chain Current.clip_index
        -> Scene Dialogue Audio Lock.current_scene

Scene Timed Audio events
        -> Scene Dialogue Audio Lock.scene_timed_audios

post-context H3 AV latent
        -> Scene Dialogue Audio Lock.av_latent

Scene Dialogue Audio Lock.dialogue_locked_av_latent
        -> H3 sampler latent_image
```

The lock should be applied after the workflow has established continuation/context
masks and before sampling.

## What is protected

Each scheduled dialogue event contributes:

1. its exact mixed waveform interval;
2. a hard-protected margin before the line;
3. a hard-protected margin after the line; and
4. an optional feather outside those hard margins.

The hard dialogue region has an audio denoise mask of zero. Outside the feather the
mask is one, so H3 can generate room tone, ambience, music, footsteps, explosions,
cloth movement, environmental sounds, and other prompted audio.

## Important output distinction

`dialogue_reference_audio` is only the deterministic supplied-dialogue stem. Silence
outside the lines is intentional and exists only as the encoding/reference canvas.
Do **not** mux this output over H3's generated soundtrack. Decode/use the sampled H3
audio for the final scene soundtrack.

## Empty scenes

For film production, `empty_scene_policy = generate` is usually correct. Scenes with
no dialogue pass through unchanged and H3 generates audio normally. Use `error` only
when every scene is expected to contain scheduled dialogue and a missing line should
stop the render.

## Continuation safety

The node preserves any upstream video denoise mask and combines its audio mask with
existing upstream audio protection. Fully generative regions retain the incoming H3
audio latent; they are not replaced by an encoded-silence latent.

This matters for recursive Context Loop workflows, where the incoming target may
already contain protected continuation material.

## Suggested starting values

```text
protect_before_ms = 200
protect_after_ms  = 250
feather_ms        = 100
mix_policy        = prevent_clipping
overflow_policy   = error
empty_scene_policy = generate
```

Adjust margins if a voice has audible breaths or consonant tails that extend outside
the nominal event boundary.

## Milestone acceptance criteria

This implementation is accepted only when all of the following remain true:

- existing full-lock node IDs, inputs, and outputs remain compatible;
- full locking preserves an upstream video denoise mask while locking all audio;
- dialogue locking protects the supplied dialogue core and leaves gaps denoisable;
- fully generative gaps retain the incoming H3 target audio latent;
- existing upstream audio protection is never weakened;
- scenes without dialogue can pass through without invoking the Audio VAE;
- `dialogue_reference_audio` is explicitly documented as non-final;
- normalized and flattened ComfyUI Autogrow forms remain supported;
- malformed H3 latents, masks, scene indices, margins, and non-finite values fail
  loudly before expensive sampling;
- no node reads arbitrary paths, starts background work, accesses the network, or
  creates persistent files/caches;
- repository test/compile artifacts remain ignored by Git.
