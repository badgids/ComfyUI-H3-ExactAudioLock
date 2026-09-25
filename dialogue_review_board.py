# Copyright (c) 2026 Alan Guice (Badgids)
# SPDX-License-Identifier: MIT

"""Batch dialogue timeline compilation, review, and Context-Loop scene selection."""

from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any

import torch
import torchaudio

from comfy_api.latest import io

from .audio_review_gate import (
    _cleanup_owned_review_files,
    _owned_review_dir,
    _preview_batch_item,
    _temp_root,
    _validate_review_audio,
)

VIDEO_FPS = 24
MAX_DIALOGUE_EVENTS = 100
H3_CHAIN_PLAN = io.Custom("H3_CHAIN_PLAN")
H3_DIALOGUE_EVENT_SET = io.Custom("H3_DIALOGUE_EVENT_SET")

_REVIEW_EVENT = "h3_exact_audio_lock.dialogue_review"
_PENDING_ROUTE = "/h3_exact_audio_lock/dialogue-review/pending"
_DECISION_ROUTE = "/h3_exact_audio_lock/dialogue-review/decision"

_PENDING_REVIEWS: dict[str, dict[str, Any]] = {}
_PENDING_LOCK = threading.Lock()
_ROUTES_REGISTERED = False

_DIALOGUE_RE = re.compile(r"<d(?:\s+[^>]*)?>(.*?)</d>", re.IGNORECASE | re.DOTALL)
_PRECEDING_SPEAKER_RE = re.compile(r"([A-Za-z0-9][A-Za-z0-9 ._'’-]{0,39})\s*:\s*$")
_INLINE_SPEAKER_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9 ._'’-]{0,39})\s*:\s*(.+)$", re.DOTALL)


def _autogrow_items(group: Any) -> list[tuple[str, Any]]:
    if group is None:
        return []
    if hasattr(group, "items"):
        rows = list(group.items())
    elif isinstance(group, (list, tuple)):
        rows = [(str(i), value) for i, value in enumerate(group)]
    else:
        rows = [("0", group)]

    def key(row: tuple[str, Any]) -> tuple[int, str]:
        name = str(row[0])
        match = re.search(r"(\d+)$", name)
        return (int(match.group(1)) if match else 10**12, name)

    return sorted(rows, key=key)


def _runtime_autogrow_items(
    group: Any,
    runtime_inputs: dict[str, Any],
    *,
    group_name: str,
) -> list[tuple[str, Any]]:
    merged = {str(name): value for name, value in _autogrow_items(group)}
    prefix = f"{group_name}."
    for runtime_name, value in runtime_inputs.items():
        runtime_name = str(runtime_name)
        if not runtime_name.startswith(prefix):
            continue
        socket_name = runtime_name[len(prefix):]
        if not socket_name:
            raise ValueError(f"Malformed {group_name} Autogrow runtime input {runtime_name!r}.")
        if socket_name in merged and merged[socket_name] is not value:
            raise ValueError(
                f"Duplicate {group_name} Autogrow input for {socket_name!r} was supplied "
                "in both normalized and flattened forms."
            )
        merged[socket_name] = value
    return _autogrow_items(merged)


def _speaker_and_text(scene_prompt: str, match: re.Match[str]) -> tuple[str, str]:
    text = re.sub(r"\s+", " ", match.group(1)).strip()
    if not text:
        raise ValueError("Context-Loop prompt contains an empty <d>...</d> dialogue tag.")

    inline = _INLINE_SPEAKER_RE.match(text)
    if inline:
        speaker = inline.group(1).strip()
        line = inline.group(2).strip()
        if line:
            return speaker, line

    prefix = scene_prompt[max(0, match.start() - 80):match.start()]
    preceding = _PRECEDING_SPEAKER_RE.search(prefix)
    if preceding:
        return preceding.group(1).strip(), text

    return "", text


def _extract_plan_dialogue(plan: Any) -> list[dict[str, Any]]:
    if not isinstance(plan, dict):
        raise ValueError(
            "MiniMax H3 Dialogue Timeline requires the H3_CHAIN_PLAN output from "
            "MiniMax H3 Chain Plan / Plan (Modern)."
        )
    shots = plan.get("shots")
    if not isinstance(shots, list) or not shots:
        raise ValueError("Context-Loop plan contains no shots.")

    rows: list[dict[str, Any]] = []
    for fallback_scene, shot in enumerate(shots, 1):
        if not isinstance(shot, dict):
            raise ValueError(f"Context-Loop shot {fallback_scene} is not an object.")
        scene_index = int(shot.get("index", fallback_scene))
        if scene_index < 1:
            raise ValueError(f"Context-Loop shot {fallback_scene} has an invalid scene index.")
        shot_id = str(shot.get("id") or f"scene_{scene_index}")
        # Use scene_prompt so dialogue in a shared prefix is not duplicated into every scene.
        scene_prompt = str(shot.get("scene_prompt", shot.get("prompt", "")) or "")
        raw_frames = int(shot.get("raw_frames", shot.get("length", 0)) or 0)
        delivered_frames = int(shot.get("delivered_frames", raw_frames) or raw_frames)
        if raw_frames <= 0:
            raise ValueError(
                f'Context-Loop scene {scene_index} "{shot_id}" does not expose a positive raw frame count.'
            )
        if delivered_frames <= 0 or delivered_frames > raw_frames:
            raise ValueError(
                f'Context-Loop scene {scene_index} "{shot_id}" has invalid delivered/raw frames.'
            )
        context_frames = raw_frames - delivered_frames

        scene_line = 0
        for match in _DIALOGUE_RE.finditer(scene_prompt):
            scene_line += 1
            speaker, text = _speaker_and_text(scene_prompt, match)
            rows.append({
                "scene_index": scene_index,
                "shot_id": shot_id,
                "scene_line_index": scene_line,
                "speaker": speaker,
                "text": text,
                "raw_frames": raw_frames,
                "delivered_frames": delivered_frames,
                "context_frames": context_frames,
                "prompt_hash": str(shot.get("prompt_hash") or ""),
            })
    if not rows:
        raise ValueError(
            "No <d>...</d> dialogue tags were found in the Context-Loop scene prompts."
        )
    if len(rows) > MAX_DIALOGUE_EVENTS:
        raise ValueError(
            f"Dialogue Timeline found {len(rows)} lines; the current limit is "
            f"{MAX_DIALOGUE_EVENTS} events."
        )
    return rows


def _flatten_audio_inputs(
    audios: Any,
    runtime_inputs: dict[str, Any],
) -> list[dict[str, Any]]:
    values: list[Any] = []
    for _name, value in _runtime_autogrow_items(
        audios, runtime_inputs, group_name="audios"
    ):
        if value is not None:
            values.append(value)

    flattened: list[dict[str, Any]] = []
    for value in values:
        waveform, sample_rate = _validate_review_audio(value)
        for batch_index in range(int(waveform.shape[0])):
            if int(waveform.shape[0]) == 1:
                audio = value
            else:
                audio = {
                    "waveform": waveform[batch_index:batch_index + 1],
                    "sample_rate": sample_rate,
                }
            flattened.append(audio)
    return flattened


def _audio_duration_frames(audio: Any) -> int:
    waveform, sample_rate = _validate_review_audio(audio)
    if int(waveform.shape[0]) != 1:
        raise ValueError("Dialogue Timeline expects flattened single-batch AUDIO events.")
    samples = int(waveform.shape[-1])
    return max(1, int(math.ceil(samples * VIDEO_FPS / float(sample_rate))))


def _validate_event_set(value: Any, *, require_approved: bool = False) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("Dialogue event set is missing or has an unsupported version.")
    events = value.get("events")
    if not isinstance(events, list):
        raise ValueError("Dialogue event set does not contain an events list.")
    seen: set[str] = set()
    for index, event in enumerate(events, 1):
        if not isinstance(event, dict):
            raise ValueError(f"Dialogue event {index} is not an object.")
        event_id = str(event.get("event_id") or "")
        if not event_id or event_id in seen:
            raise ValueError("Dialogue event IDs must be non-empty and unique.")
        seen.add(event_id)
        scene_index = int(event.get("scene_index", 0))
        start_frame = int(event.get("start_frame", -1))
        duration_frames = int(event.get("duration_frames", 0))
        raw_frames = int(event.get("raw_frames", 0))
        if scene_index < 1 or start_frame < 0 or duration_frames < 1 or raw_frames < 1:
            raise ValueError(f"Dialogue event {event_id!r} has invalid timing metadata.")
        if start_frame + duration_frames > raw_frames:
            raise ValueError(
                f"Dialogue event {event_id!r} ends at frame "
                f"{start_frame + duration_frames}, beyond scene length {raw_frames}."
            )
        if require_approved and not bool(event.get("approved")):
            raise ValueError(f"Dialogue event {event_id!r} has not been approved.")
        _validate_review_audio(event.get("audio"))
    return value


def _event_set_summary(value: dict[str, Any]) -> str:
    events = value["events"]
    scene_count = len({int(event["scene_index"]) for event in events})
    approved = sum(1 for event in events if event.get("approved"))
    return (
        f"{len(events)} dialogue event(s) across {scene_count} scene(s); "
        f"{approved} approved."
    )


class MiniMaxH3DialogueTimeline(io.ComfyNode):
    """Compile Context-Loop <d> dialogue and matching AUDIO into one event set."""

    @classmethod
    def define_schema(cls) -> io.Schema:
        audio_template = io.Autogrow.TemplatePrefix(
            input=io.Audio.Input("audio"),
            prefix="audio_",
            min=1,
            max=MAX_DIALOGUE_EVENTS,
        )
        return io.Schema(
            node_id="MiniMaxH3DialogueTimeline",
            display_name="MiniMax H3 Dialogue Timeline",
            category="MiniMax H3/Audio",
            description=(
                "Read <d>...</d> dialogue from a Context-Loop H3_CHAIN_PLAN, map "
                "AUDIO inputs to lines in plan order, and create scene-aware timing."
            ),
            inputs=[
                H3_CHAIN_PLAN.Input(
                    "plan",
                    tooltip=(
                        "Validated plan output from MiniMax H3 Chain Plan / Plan (Modern)."
                    ),
                ),
                io.Autogrow.Input(
                    "audios",
                    template=audio_template,
                    tooltip=(
                        "One AUDIO per <d> dialogue line, in production-plan order. "
                        "Batched AUDIO is flattened in batch order."
                    ),
                ),
                io.Int.Input(
                    "initial_lead_frames",
                    default=12,
                    min=0,
                    max=1_000_000,
                    step=1,
                    tooltip=(
                        "Automatic first-line lead inside each scene's delivered region. "
                        "Continuation head-context frames are added automatically."
                    ),
                ),
                io.Int.Input(
                    "gap_frames",
                    default=8,
                    min=0,
                    max=1_000_000,
                    step=1,
                    tooltip="Automatic gap between consecutive dialogue clips in one scene.",
                ),
                io.Float.Input(
                    "gain_db",
                    default=0.0,
                    min=-60.0,
                    max=24.0,
                    step=0.1,
                    tooltip="Default gain recorded on every compiled event.",
                ),
            ],
            outputs=[
                H3_DIALOGUE_EVENT_SET.Output(display_name="dialogue_event_set"),
                io.Int.Output(display_name="event_count"),
                io.String.Output(display_name="summary"),
            ],
        )

    @classmethod
    def execute(
        cls,
        plan,
        audios: io.Autogrow.Type | None = None,
        initial_lead_frames: int = 12,
        gap_frames: int = 8,
        gain_db: float = 0.0,
        **runtime_inputs,
    ) -> io.NodeOutput:
        dialogue = _extract_plan_dialogue(plan)
        audio_rows = _flatten_audio_inputs(audios, runtime_inputs)
        if len(audio_rows) != len(dialogue):
            raise ValueError(
                "Dialogue Timeline requires exactly one AUDIO event per <d> dialogue line: "
                f"plan has {len(dialogue)} line(s), received {len(audio_rows)} AUDIO event(s)."
            )

        lead = int(initial_lead_frames)
        gap = int(gap_frames)
        gain = float(gain_db)
        if lead < 0 or gap < 0 or not math.isfinite(gain):
            raise ValueError("Dialogue Timeline lead/gap must be non-negative and gain_db finite.")

        scene_cursors: dict[int, int] = {}
        events: list[dict[str, Any]] = []
        for global_index, (meta, audio) in enumerate(zip(dialogue, audio_rows), 1):
            scene = int(meta["scene_index"])
            duration = _audio_duration_frames(audio)
            cursor = scene_cursors.setdefault(
                scene, int(meta["context_frames"]) + lead
            )
            start = int(cursor)
            raw_frames = int(meta["raw_frames"])
            if start + duration > raw_frames:
                raise ValueError(
                    f'Dialogue line {global_index} in scene {scene} "{meta["shot_id"]}" '
                    f"does not fit the automatic layout: start={start}, duration={duration}, "
                    f"scene length={raw_frames}. Reduce lead/gap, shorten the audio, increase "
                    "the scene length, or author fewer lines in that scene."
                )
            scene_cursors[scene] = start + duration + gap
            event_id = f"scene_{scene:03d}_line_{int(meta['scene_line_index']):03d}"
            events.append({
                **meta,
                "event_id": event_id,
                "global_line_index": global_index,
                "audio": audio,
                "start_frame": start,
                "duration_frames": duration,
                "gain_db": gain,
                "label": (
                    f"{meta['speaker']}: {meta['text']}"
                    if meta["speaker"] else meta["text"]
                ),
                "approved": False,
            })

        plan_hash = str(plan.get("plan_hash") or "")
        if not plan_hash:
            plan_hash = hashlib.sha256(
                "|".join(str(event.get("prompt_hash") or "") for event in events).encode("utf-8")
            ).hexdigest()
        result = {
            "version": 1,
            "scope": "production",
            "plan_hash": plan_hash,
            "events": events,
        }
        return io.NodeOutput(result, len(events), _event_set_summary(result))


def _create_previews(
    events: list[dict[str, Any]], token: str
) -> tuple[list[dict[str, Any]], list[Path], Path, Path]:
    temp_root = _temp_root()
    review_dir = _owned_review_dir(temp_root, token)
    review_dir.mkdir(parents=True, exist_ok=False)
    paths: list[Path] = []
    previews: list[dict[str, Any]] = []
    try:
        for index, event in enumerate(events, 1):
            waveform, sample_rate = _validate_review_audio(event["audio"])
            if int(waveform.shape[0]) != 1:
                raise ValueError("Dialogue review events must contain single-batch AUDIO.")
            preview_waveform, warning = _preview_batch_item(waveform[0])
            filename = f"dialogue_{index:03d}.wav"
            path = review_dir / filename
            torchaudio.save(
                str(path),
                preview_waveform,
                sample_rate,
                encoding="PCM_F",
                bits_per_sample=32,
            )
            paths.append(path)
            previews.append({
                "event_id": event["event_id"],
                "scene_index": int(event["scene_index"]),
                "shot_id": str(event["shot_id"]),
                "speaker": str(event.get("speaker") or ""),
                "text": str(event["text"]),
                "start_frame": int(event["start_frame"]),
                "duration_frames": int(event["duration_frames"]),
                "raw_frames": int(event["raw_frames"]),
                "delivered_frames": int(event["delivered_frames"]),
                "context_frames": int(event["context_frames"]),
                "filename": filename,
                "subfolder": f"h3_exact_audio_lock_review/{token}",
                "type": "temp",
                "sample_rate": sample_rate,
                "warning": warning,
            })
    except Exception:
        _cleanup_owned_review_files(paths, review_dir, temp_root)
        raise
    return previews, paths, review_dir, temp_root


def _public_review(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "token": record["token"],
        "label": record["label"],
        "created_at": record["created_at"],
        "deadline": record["deadline"],
        "node_id": record.get("node_id", ""),
        "events": record["previews"],
    }


def _list_pending_dialogue_reviews() -> list[dict[str, Any]]:
    with _PENDING_LOCK:
        return [
            _public_review(record)
            for record in _PENDING_REVIEWS.values()
            if record.get("action") is None
        ]


def _submit_dialogue_review_decision(
    token: str,
    action: str,
    event_edits: Any = None,
) -> tuple[bool, str]:
    token = str(token or "")
    action = str(action or "").lower()
    if action not in {"commit", "reject"}:
        return False, "invalid_action"

    with _PENDING_LOCK:
        record = _PENDING_REVIEWS.get(token)
        if record is None:
            return False, "not_found"
        if record.get("action") is not None:
            return False, "already_decided"

        if action == "reject":
            record["action"] = "reject"
            record["event_edits"] = None
            event = record["event"]
        else:
            if not isinstance(event_edits, list):
                return False, "invalid_event_edits"
            expected = {item["event_id"]: item for item in record["previews"]}
            edits: dict[str, int] = {}
            for row in event_edits:
                if not isinstance(row, dict):
                    return False, "invalid_event_edits"
                event_id = str(row.get("event_id") or "")
                if event_id not in expected or event_id in edits:
                    return False, "invalid_event_edits"
                if row.get("approved") is not True:
                    return False, "all_events_must_be_approved"
                try:
                    start_frame = int(row.get("start_frame"))
                except (TypeError, ValueError):
                    return False, "invalid_start_frame"
                source = expected[event_id]
                if start_frame < 0:
                    return False, "invalid_start_frame"
                if start_frame + int(source["duration_frames"]) > int(source["raw_frames"]):
                    return False, "event_exceeds_scene"
                edits[event_id] = start_frame
            if set(edits) != set(expected):
                return False, "all_events_must_be_approved"
            record["action"] = "commit"
            record["event_edits"] = edits
            event = record["event"]

    event.set()
    return True, "ok"


def _notify_review(payload: dict[str, Any]) -> None:
    from server import PromptServer
    PromptServer.instance.send_sync(_REVIEW_EVENT, payload)


def register_dialogue_review_routes() -> None:
    global _ROUTES_REGISTERED
    if _ROUTES_REGISTERED:
        return

    from aiohttp import web
    from server import PromptServer

    routes = PromptServer.instance.routes

    @routes.get(_PENDING_ROUTE)
    async def pending_dialogue_reviews(_request):
        return web.json_response({"reviews": _list_pending_dialogue_reviews()})

    @routes.post(_DECISION_ROUTE)
    async def decide_dialogue_review(request):
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid_json"}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"ok": False, "error": "invalid_body"}, status=400)

        ok, reason = _submit_dialogue_review_decision(
            body.get("token"),
            body.get("action"),
            body.get("events"),
        )
        if ok:
            return web.json_response({"ok": True})
        status = 409 if reason == "already_decided" else 404 if reason == "not_found" else 400
        return web.json_response({"ok": False, "error": reason}, status=status)

    _ROUTES_REGISTERED = True


class DialogueReviewApprovalBoard(io.ComfyNode):
    """Review every required dialogue line once, then release the full approved set."""

    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="H3ExactAudioLockDialogueReviewBoard",
            display_name="Dialogue Review / Approval Board",
            category="MiniMax H3/Audio",
            description=(
                "One production-wide review barrier for many required dialogue events. "
                "Every event must be approved; start frames can be adjusted before H3 runs."
            ),
            inputs=[
                H3_DIALOGUE_EVENT_SET.Input("dialogue_event_set"),
                io.String.Input(
                    "review_label",
                    default="Dialogue preflight",
                    multiline=False,
                ),
                io.Int.Input(
                    "review_timeout_seconds",
                    default=86400,
                    min=30,
                    max=86400,
                    step=30,
                ),
            ],
            outputs=[
                H3_DIALOGUE_EVENT_SET.Output(display_name="approved_dialogue_set"),
                io.Int.Output(display_name="approved_event_count"),
                io.String.Output(display_name="summary"),
            ],
            hidden=[io.Hidden.unique_id],
            is_output_node=True,
        )

    @classmethod
    def execute(
        cls,
        dialogue_event_set,
        review_label: str = "Dialogue preflight",
        review_timeout_seconds: int = 86400,
        unique_id=None,
        **runtime_inputs,
    ) -> io.NodeOutput:
        source = _validate_event_set(dialogue_event_set, require_approved=False)
        if not source["events"]:
            raise ValueError("Dialogue Review / Approval Board requires at least one event.")

        timeout = float(review_timeout_seconds)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("review_timeout_seconds must be positive and finite.")

        token = secrets.token_urlsafe(18)
        previews, paths, review_dir, temp_root = _create_previews(source["events"], token)
        event = threading.Event()
        now = time.time()
        node_id = str(unique_id or runtime_inputs.pop("unique_id", "") or "")
        record = {
            "token": token,
            "label": str(review_label or ""),
            "created_at": now,
            "deadline": now + timeout,
            "node_id": node_id,
            "previews": previews,
            "paths": paths,
            "review_dir": review_dir,
            "temp_root": temp_root,
            "event": event,
            "action": None,
            "event_edits": None,
        }
        with _PENDING_LOCK:
            _PENDING_REVIEWS[token] = record

        try:
            try:
                _notify_review(_public_review(record))
            except Exception as exc:
                _cleanup_owned_review_files(paths, review_dir, temp_root)
                raise RuntimeError(
                    "Dialogue review UI notification failed; previews were cleaned up."
                ) from exc

            decided = event.wait(timeout)
            action = record.get("action")
            if not decided or action is None:
                _cleanup_owned_review_files(paths, review_dir, temp_root)
                raise TimeoutError(
                    "Dialogue Review / Approval Board timed out before all dialogue was approved."
                )
            if action != "commit":
                raise RuntimeError("Dialogue review was rejected. H3 generation was not released.")

            edits = dict(record.get("event_edits") or {})
            approved_events: list[dict[str, Any]] = []
            for original in source["events"]:
                row = dict(original)
                row["start_frame"] = int(edits[row["event_id"]])
                row["approved"] = True
                approved_events.append(row)

            approved = dict(source)
            approved["events"] = approved_events
            approved["approved"] = True
            approved["approval_count"] = len(approved_events)
            approved["approval_revision"] = hashlib.sha256(
                json.dumps(
                    [
                        {
                            "event_id": row["event_id"],
                            "scene_index": row["scene_index"],
                            "start_frame": row["start_frame"],
                            "prompt_hash": row.get("prompt_hash", ""),
                        }
                        for row in approved_events
                    ],
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            _validate_event_set(approved, require_approved=True)
            return io.NodeOutput(
                approved,
                len(approved_events),
                _event_set_summary(approved),
            )
        finally:
            _cleanup_owned_review_files(paths, review_dir, temp_root)
            with _PENDING_LOCK:
                if _PENDING_REVIEWS.get(token) is record:
                    _PENDING_REVIEWS.pop(token, None)


class MiniMaxH3CurrentSceneDialogue(io.ComfyNode):
    """Filter one approved production dialogue set to the active Context-Loop scene."""

    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="MiniMaxH3CurrentSceneDialogue",
            display_name="MiniMax H3 Current Scene Dialogue",
            category="MiniMax H3/Audio",
            description=(
                "Filter an approved production-wide dialogue set by Context-Loop "
                "Chain Current.clip_index. Only the active scene's lines continue."
            ),
            inputs=[
                H3_DIALOGUE_EVENT_SET.Input("approved_dialogue_set"),
                io.Int.Input(
                    "current_scene",
                    default=1,
                    min=1,
                    max=1_000_000,
                    step=1,
                    tooltip="Connect MiniMax H3 Chain Current.clip_index here.",
                ),
            ],
            outputs=[
                H3_DIALOGUE_EVENT_SET.Output(display_name="current_scene_dialogue_set"),
                io.Int.Output(display_name="event_count"),
                io.String.Output(display_name="summary"),
            ],
        )

    @classmethod
    def execute(cls, approved_dialogue_set, current_scene: int) -> io.NodeOutput:
        source = _validate_event_set(approved_dialogue_set, require_approved=True)
        scene = int(current_scene)
        if scene < 1:
            raise ValueError("current_scene must be a one-based scene index.")
        selected = [
            dict(event)
            for event in source["events"]
            if int(event["scene_index"]) == scene
        ]
        result = {
            "version": 1,
            "scope": "scene",
            "scene_index": scene,
            "plan_hash": source.get("plan_hash", ""),
            "approval_revision": source.get("approval_revision", ""),
            "approved": True,
            "events": selected,
        }
        summary = (
            f"scene {scene}: {len(selected)} approved dialogue event(s)"
            if selected else f"scene {scene}: no approved dialogue events"
        )
        return io.NodeOutput(result, len(selected), summary)
