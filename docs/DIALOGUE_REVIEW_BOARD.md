# Dialogue Timeline and Approval Board

`MiniMax H3 Dialogue Timeline`, `Dialogue Review / Approval Board`, and
`MiniMax H3 Current Scene Dialogue` provide a production-scale dialogue path for
MiniMax H3 Context Loop workflows.

They solve a different problem from `Audio Review / Accept Gate`.

- `Audio Review / Accept Gate` chooses one candidate for one logical audio event.
- `Dialogue Review / Approval Board` reviews many required dialogue events and
  releases the complete approved set only after every event is approved.

## Context Loop contract

The timeline node consumes the validated `H3_CHAIN_PLAN` output from Context Loop.
It reads dialogue from each scene's `scene_prompt` using `<d>...</d>` tags.

Example:

```text
Kendra turns toward Jinx.
Kendra: <d>Where the hell have you been?</d>

Jinx drops her bag.
Jinx: <d>You really don't want to know.</d>
```

The same speaker can also be written inside the tag:

```text
<d>Kendra: Where the hell have you been?</d>
```

If no speaker label can be found, the event is still valid; the review board shows
an empty speaker field and uses the dialogue text as its label.

The timeline maps connected `AUDIO` values to dialogue tags in production-plan
order:

```text
scene 1 line 1 -> audio_0
scene 1 line 2 -> audio_1
scene 2 line 1 -> audio_2
...
```

The count is strict. If the plan contains 52 `<d>` tags, the node must receive
exactly 52 flattened AUDIO events.

## Initial timing

Natural-language prompt order does not contain an exact start frame. The timeline
therefore creates an initial deterministic layout using:

- the shot's Context Loop `raw_frames`;
- the shot's repeated head-context length (`raw_frames - delivered_frames`);
- `initial_lead_frames`;
- the real duration of each AUDIO clip; and
- `gap_frames`.

For a continuation scene, automatic dialogue never begins inside the repeated
head-context unless the user later deliberately moves it there.

The initial event start is:

```text
context_frames + initial_lead_frames
```

Subsequent lines begin after the preceding AUDIO duration plus `gap_frames`.

If the automatic layout cannot fit a line inside its scene, the timeline fails
before review. Shorten the audio, reduce lead/gap, increase scene length, or reduce
the number of lines assigned to that scene.

## Review barrier

`Dialogue Review / Approval Board` is an output node and a normal data dependency.
When queued, it:

1. creates temporary browser-playable previews for every event;
2. presents all required dialogue in one embedded review table;
3. lets the user play every line;
4. lets the user adjust the exact raw scene-local `start_frame`;
5. requires every event to be approved; and
6. blocks downstream H3 execution until `Commit approved dialogue` is pressed.

Rejecting the review stops the execution. Timeout also fails closed.

Temporary previews are cleaned after commit, reject, timeout, or failure. The
workflow's original AUDIO objects remain the data path.

## Context Loop scene routing

Connect:

```text
Dialogue Review / Approval Board.approved_dialogue_set
        -> MiniMax H3 Current Scene Dialogue.approved_dialogue_set

MiniMax H3 Chain Current.clip_index
        -> MiniMax H3 Current Scene Dialogue.current_scene
```

The selector outputs only events whose `scene_index` equals the current one-based
Context Loop `clip_index`.

Then connect:

```text
MiniMax H3 Current Scene Dialogue.current_scene_dialogue_set
        -> MiniMax H3 Scene Dialogue Audio Lock.dialogue_event_set
```

or:

```text
MiniMax H3 Current Scene Dialogue.current_scene_dialogue_set
        -> MiniMax H3 Scene Exact Audio Lock.dialogue_event_set
```

The existing `scene_timed_audios` Autogrow input remains available for backward
compatibility. A lock may consume the new event set, legacy Scene Timed Audio
events, or both. Do not intentionally duplicate the same event through both paths.

## One source of scene identity and timing

The production-wide approved set stores:

```text
event_id
scene_index
shot_id
speaker
text
start_frame
duration_frames
gain_db
audio
prompt_hash
approved
```

`scene_index` decides which Context Loop iteration receives the event.

`start_frame` decides where inside that raw H3 scene target the event begins.

For continuation scenes, `start_frame` includes the repeated head-context frames.
The review UI shows the context-frame count so the user can see where delivered
content begins.

## Multiple speakers and overlap

All approved events for the current scene are passed together to the scene lock.
Multiple speakers and intentional overlap are supported.

Use:

```text
mix_policy = sum
```

or:

```text
mix_policy = prevent_clipping
```

for intentional talk-over.

Do not use `reject_overlap` when overlapping speech is intentional.

## Dialogue-partial finalization

For `MiniMax H3 Scene Dialogue Audio Lock`, the final audio path remains:

```text
Sampler -> VAEDecodeAudio ---------------------\
Scene Dialogue Lock.dialogue_reference_audio ---+-> MiniMax H3 Dialogue Audio Finalize
Scene Dialogue Lock.dialogue_lock_manifest -----/
```

The finalizer restores the supplied dialogue samples at the approved scheduled
intervals while keeping H3-generated audio outside those intervals.

## Plan edits and re-review

The timeline carries Context Loop prompt hashes and the plan hash into the event set.
Because the approved board depends on the compiled event set, changing the plan,
dialogue text, scene structure, AUDIO inputs, or automatic layout inputs invalidates
that upstream data and requires a new review instead of silently reusing an old
timeline.

## Limits

The current hard limit is 100 dialogue events per production event set, matching
ComfyUI's native Autogrow hard limit.

This is a workflow/UI bound, not a recommendation to place 100 dialogue lines in
one H3 scene.
