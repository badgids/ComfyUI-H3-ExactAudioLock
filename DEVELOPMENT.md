# Development Guide

This repository is a ComfyUI custom-node package. Development must preserve the
public node IDs and data contracts used by saved workflows.

## Supported runtime assumptions

- Current ComfyUI with `comfy_api.latest` and native `Autogrow`.
- Native MiniMax H3 joint AV latents.
- PyTorch and TorchAudio from the active ComfyUI Python environment.
- Python 3.11+.

The package must not depend on developer-specific filesystem paths, environment
variables, a separate virtual environment, or imports from another custom-node
package.

## Compatibility rules

1. Existing node IDs and existing input/output names are backward-compatible API.
2. New recursive-workflow integration must consume normal ComfyUI types (`AUDIO`,
   `INT`, `LATENT`, `VAE`) rather than importing H3 Context Loop internals.
3. H3 scene numbers are one-based. H3 Context Loop's `clip_index` can be wired to
   `MiniMax H3 Scene Exact Audio Lock.current_scene`.
4. ExactAudioLock owns the complete target audio latent when enabled. Do not also
   enable another complete target-audio lock for the same sampler invocation.
5. No node in this package may open arbitrary user-supplied paths. Audio enters as
   a normal ComfyUI `AUDIO` value.
6. Runtime Autogrow values must support both normalized mappings and ComfyUI V3's
   flattened `group.socket_N` execution form.
7. Invalid or ambiguous audio data must fail before Audio-VAE encoding rather than
   silently dropping batches or propagating NaN/Inf samples.

See `PROTOCOL.md` for the frozen data contracts.

## Tests

Run tests with the Python interpreter used by ComfyUI so PyTorch and TorchAudio
are already available:

```bash
cd /path/to/ComfyUI/custom_nodes/ComfyUI-H3-ExactAudioLock
python -m unittest discover -s tests -v
```

The tests stub only ComfyUI's schema/extension plumbing. They exercise the actual
audio validation, scene filtering, flattened Autogrow handling, H3 latent-shape
checks, audio/video denoise-mask behavior, and node registration.

Before committing a patch:

```bash
python -m compileall -q .
python -m unittest discover -s tests -v
git diff --check
```

## Review checklist

For every milestone, explicitly review:

- acceptance criteria and backward compatibility;
- ComfyUI V3 schema/Autogrow behavior;
- native MiniMax H3 latent shape and mask semantics;
- scene numbering and recursive-loop behavior;
- malformed inputs and empty-scene behavior;
- overlapping/overflow audio behavior;
- batch handling and non-finite samples;
- filesystem/network access and hard-coded paths;
- caches, temporary files, background work, and cleanup obligations;
- README/protocol/development documentation;
- tests for success paths and error paths.

This package currently creates no files, starts no threads/processes, opens no
network connections, and maintains no persistent cache, so there is no package
cleanup routine to run.

## Partial-dialogue lock milestone

Partial dialogue locking is a separate public behavior from the complete target-audio
lock. The partial-lock nodes must preserve incoming video masks and any upstream
audio constraints, must leave non-dialogue target regions generative, and must never
label the dialogue reference stem as the final scene soundtrack.

For partial locking, mask semantics are `1 = denoise/generate` and `0 = protect`.
The dialogue core and configured hard margins are zero; optional feather regions are
fractional transitions back toward one. Fully generative regions retain the incoming
H3 audio latent rather than being replaced by encoded silence.

Repository test/compile runs create Python bytecode caches. `.gitignore` excludes
those generated artifacts so the working tree remains clean after the required
verification commands.
