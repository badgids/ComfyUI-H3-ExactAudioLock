# Audio Review / Accept Gate

`Audio Review / Accept Gate` is a Context-Loop-style human review checkpoint for
choosing one take from multiple audio candidates before the chosen audio continues
through the workflow.

**Class ID:** `H3ExactAudioLockAudioReviewGate`

The gate is deliberately engine-agnostic. Connected candidates use ComfyUI's normal
`AUDIO` contract. File candidates are selected/uploaded through ComfyUI's managed
input directory and decoded into the same `AUDIO` contract.

## Candidate-review behavior

A review can contain one or many candidates. The browser UI presents them as a
candidate carousel with one audio player visible at a time.

For each review you can:

- move backward and forward through the candidates;
- listen to every candidate before deciding;
- mark any non-winning takes with **Save this take as an alternate**;
- choose the active candidate with **Accept selected & continue**; or
- use **Reject all & stop** when none of the candidates should continue.

Only the selected accepted candidate is returned on `accepted_audio`. Previously
accepted candidates, other candidates in the current review, and saved alternates
are never concatenated or emitted together with it.

Marked alternates are persisted separately under:

```text
ComfyUI/output/h3_exact_audio_lock_review/alternates/<review-token>/
```

Connected in-memory `AUDIO` alternates are saved as lossless float WAV files. Direct
file candidates are copied in their original MP3/FLAC/OGG/OGA/Opus/WAV format.
The accepted take is not duplicated into the alternate folder even if it had been
marked before the user clicked Accept.

## Inputs and candidate sources

The node supports two source modes.

### `connected_audio`

Use normal ComfyUI `AUDIO` outputs from TTS, music, loaders, or processing nodes.

- `audio` is the first optional candidate.
- `audio_candidates` is a native ComfyUI Autogrow group for additional candidates.
- A connected `AUDIO` whose waveform batch dimension is greater than one is split
  into individual review candidates.

This supports Qwen3-TTS, ACE-Step, YuE/YuE2, default ComfyUI audio nodes, and any
other node pack that exposes a normal `AUDIO` output.

### `audio_file`

Use files from ComfyUI's managed input directory.

- `audio_file` is the first optional upload/selection candidate.
- `audio_files` is a native ComfyUI Autogrow group for additional file candidates.

Supported extensions:

- `.wav`
- `.mp3`
- `.flac`
- `.ogg`
- `.oga`
- `.opus`

Files are decoded with PyAV, matching ComfyUI's built-in Load Audio stack, and are
not resampled by the gate.

## Loop behavior

Each gate invocation owns exactly one review batch. This is important for looping or
recursive workflows:

```text
review batch 1 -> choose B -> output B only -> review state removed
review batch 2 -> choose D -> output D only -> review state removed
review batch 3 -> ...
```

The browser keeps only pending reviews. Once a review is decided, that review is
removed from the frontend queue and backend pending map. An accepted candidate from
a prior loop iteration therefore does not remain in the next review UI and cannot be
included in the next output.

To compare several generated takes **in one review**, present them to one gate
invocation through the growable `audio_candidates` sockets or as members of a
batched `AUDIO`. For pre-rendered candidates, use the growable `audio_files` slots.
The gate intentionally does not import or control another node pack's internal
requeue mechanism; candidate generation/collection remains normal ComfyUI wiring.

This mirrors the important Context Loop Review Gate separation:

- active candidate = the take selected for continuation;
- marked/kept candidates = saved alternates;
- unselected/unkept candidates = rejected/disposable candidates.

## ExactAudioLock wiring

Connected candidates:

```text
Qwen3 TTS take A ----\
Qwen3 TTS take B -----+--> Audio Review / Accept Gate --> accepted_audio
Qwen3 TTS take C ----/                                  |
                                                         v
                                               MiniMax H3 Timed Audio
                                                         |
                                                         v
                                             MiniMax H3 Exact Audio Lock
```

Direct file candidates:

```text
take_01.mp3 ----\
take_02.flac -----+--> Audio Review / Accept Gate --> accepted_audio
take_03.ogg ----/        source_mode=audio_file
```

The output is ordinary ComfyUI `AUDIO`, so the winning candidate can feed
ExactAudioLock, H3 Context Loop companion wiring, or any unrelated downstream audio
node.

## Inputs

| Input | Purpose |
| --- | --- |
| `source_mode` | `connected_audio` for AUDIO candidates or `audio_file` for managed file candidates. |
| `audio` | Optional first connected `AUDIO` candidate. |
| `audio_candidates` | Growable group of additional connected `AUDIO` candidates. |
| `audio_file` | Optional first WAV/MP3/FLAC/OGG/OGA/Opus candidate. |
| `audio_files` | Growable group of additional managed file candidates. |
| `review_label` | Optional label displayed above the candidate carousel. |
| `delete_rejected_audio_files` | When enabled, delete unselected/unkept **managed file candidates** after an explicit decision. Connected AUDIO has no trusted source filepath and is never used to infer one. |
| `review_timeout_seconds` | Maximum time to wait for the browser review decision. |

## Output

| Output | Purpose |
| --- | --- |
| `accepted_audio` | Only the selected accepted candidate as standard ComfyUI `AUDIO`. |

There is intentionally no output containing all candidates or all saved alternates.
That prevents a loop from accidentally forwarding prior/rejected takes along with the
newly approved audio.

## Alternate retention and rejected-file deletion

The alternate and deletion rules are source-aware.

### Connected `AUDIO`

A normal `AUDIO` value does not securely identify an upstream file. Therefore:

- the selected candidate continues downstream;
- marked alternates are written as WAV files in the managed ComfyUI output folder;
- unselected/unkept candidates are dropped from this review;
- the gate deletes its temporary preview WAVs;
- the gate never guesses at or deletes files belonging to upstream TTS/music nodes.

### Direct file candidates

The user explicitly selected these files through this gate. Therefore:

- the selected file candidate is retained;
- marked alternates are copied to the managed alternate-output folder and their
  original input files are retained;
- if `delete_rejected_audio_files=true`, unselected/unkept input files are deleted
  only after their paths are revalidated inside ComfyUI's managed input directory;
- if deletion is disabled, all original file candidates remain in ComfyUI input.

`Reject all & stop` can still preserve candidates that were explicitly marked as
alternates. Unmarked managed file candidates are eligible for deletion when the
option is enabled.

## Preview behavior

Every candidate receives one temporary WAV preview under ComfyUI's managed temp
directory. The UI displays one preview player at a time and switches it as the user
moves through the candidate carousel.

Mono and stereo previews preserve their channel layout. More-than-stereo candidates
may be downmixed to stereo **for browser preview only**. That preview conversion does
not alter the connected workflow `AUDIO` selected for downstream use.

All gate-created preview files are temporary and are cleaned after accept, reject,
timeout, or infrastructure failure. They are never the downstream workflow value.

## Caching

Connected mode relies on ordinary ComfyUI input caching. A recursively requeued H3
workflow does not need to stop at the same already-approved gate again while all gate
inputs remain unchanged.

File mode fingerprints the contents of every selected file candidate. Replacing one
candidate under the same filename therefore changes the fingerprint and forces a new
review.

A rejected or failed review produces no accepted node output.

## Safety and failure behavior

The gate fails closed when:

- no candidates are supplied;
- a connected candidate has malformed, empty, non-finite, or invalid-rate AUDIO;
- a file candidate is missing, duplicated, outside ComfyUI input, unsupported, or
  undecodable;
- a preview cannot be written;
- a requested alternate cannot be persisted;
- the browser notification fails;
- the review timeout expires;
- the browser submits an unknown selected candidate or unknown alternate ID; or
- the user rejects all candidates.

Timeouts, decode failures, notification failures, and alternate-save failures never
delete the original file candidates. Temporary previews are cleaned on those failure
paths.
