# Development Guide

This repository is a ComfyUI custom-node package. Development must preserve the
public node IDs and data contracts used by saved workflows.

## Supported runtime assumptions

- Current ComfyUI with `comfy_api.latest` and native `Autogrow`.
- Native MiniMax H3 joint AV latents.
- PyTorch and TorchAudio from the active ComfyUI Python environment.
- PyAV `14.2.0` or newer, installed from the repository `requirements.txt`.
- Python 3.11+.

The package must not depend on developer-specific filesystem paths, environment
variables, a separate virtual environment, or imports from another custom-node
package.

## Dependency policy

`requirements.txt` is the standard pip-installable dependency contract for this
custom node. It currently declares only:

```text
av>=14.2.0
```

Do not add `torch` or `torchaudio` to this repository's requirements file. Those
packages belong to the ComfyUI runtime and may be installed from backend-specific
CUDA, ROCm, XPU, or other package indexes. A custom-node install must not silently
replace the user's working Torch stack.

Install or refresh this repository's dependencies with the same interpreter that
runs ComfyUI:

```bash
python -m pip install -r requirements.txt
```

See `docs/INSTALLATION.md` for venv and Windows portable examples.

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
audio validation, nonzero frame-to-sample placement, scene filtering, flattened
Autogrow handling, H3 latent-shape checks, audio/video denoise-mask behavior,
dialogue finalization, node registration, example-workflow wiring, and workflow
layout overlap checks.

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

The core lock nodes do not create persistent files or perform network access. The
Audio Review / Accept Gate is the documented exception: it creates temporary preview
files under ComfyUI temp and optional retained alternates under ComfyUI output. Those
preview files must be cleaned on accept, reject, timeout, and failure paths. The gate
does not start subprocesses or background worker threads and performs no external
network access.

## Partial-dialogue lock milestone

Partial dialogue locking is a separate public behavior from the complete target-audio
lock. The partial-lock nodes must preserve incoming video masks and any upstream
audio constraints and must leave non-dialogue target regions generative. Supplied dialogue timing remains
authoritative: the post-sampling `MiniMax H3 Dialogue Audio Finalize` node restores
the deterministic dialogue waveform at the exact manifest sample intervals while
preserving H3-generated audio outside those intervals.

For partial locking, mask semantics are `1 = denoise/generate` and `0 = protect`.
The dialogue core and configured hard margins are zero; optional feather regions are
fractional transitions back toward one. Fully generative regions retain the incoming
H3 audio latent rather than being replaced by encoded silence. The raw sampled H3
audio is not the final timing authority for supplied dialogue; partial-lock workflows
must pass sampled audio through the dialogue finalizer before final mux/assembly.

Repository test/compile runs create Python bytecode caches. `.gitignore` excludes
those generated artifacts so the working tree remains clean after the required
verification commands.

Example workflows are part of the tested public documentation. No two node bounding
boxes may overlap or hide one another, and the graph should read left-to-right without
manual rearrangement. Full-lock examples must mux the lock node's `exact_audio`;
dialogue-partial examples must mux `MiniMax H3 Dialogue Audio Finalize.final_audio`.
For standalone timed dialogue, use the Timed Audio node's `start_frame` output to
drive native `Add Guide for MiniMax H3.frame_idx` so conditioning and lock placement
share one frame value.

## Audio review gate milestone

`H3ExactAudioLockAudioReviewGate` is an engine-agnostic human review boundary for
normal ComfyUI `AUDIO` values and managed audio files. Candidate review is selection,
not concatenation: exactly one accepted candidate is emitted from `accepted_audio`;
kept alternates are saved separately and previous-loop accepts never become part of
a later review's output.

Connected candidates use native ComfyUI `AUDIO` plus Autogrow sockets and must accept
both normalized and flattened V3 runtime forms. Batched `AUDIO` may be split into
individual review candidates without mutating the source tensor. Direct file review
is confined to ComfyUI's managed input directory and supports WAV, MP3, FLAC, OGG,
OGA, and Opus. PyAV from the active ComfyUI environment performs managed-file decode.

The review gate is the package's narrow filesystem/UI exception. Disposable previews
live only under ComfyUI temp. Explicitly marked alternates live only below
`output/h3_exact_audio_lock_review/alternates/`. Connected alternates are saved as
float WAV; direct-file alternates preserve their original supported encoded format.
The selected candidate is not duplicated as an alternate.

`delete_rejected_audio_files` may delete only unselected/unkept managed input files
after an explicit review decision and only after path confinement is revalidated.
Connected `AUDIO` never causes an inferred upstream filepath deletion. Timeouts,
decode failures, notification failures, and alternate-save failures retain source
files and clean gate-owned previews. The gate starts no subprocess or background
worker thread and performs no external network access; review communication is
same-origin through ComfyUI.

Tests for this milestone must cover candidate selection, alternate retention, batch
splitting, Autogrow normalized/flattened inputs, managed-file decoding/fingerprints,
path confinement, deletion ownership, sequential-loop isolation, timeout/failure
cleanup, frontend node routing, and the guarantee that only the selected candidate
leaves `accepted_audio`.
