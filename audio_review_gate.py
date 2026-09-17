# Copyright (c) 2026 Alan Guice (Badgids)
# SPDX-License-Identifier: MIT

"""Interactive review/accept gate for normal ComfyUI AUDIO values.

The gate is intentionally engine-agnostic. It accepts and returns ComfyUI's normal
AUDIO contract and creates temporary WAV copies only for browser playback. The
accepted AUDIO object is returned unchanged; preview encoding never enters the
workflow data path.
"""
from __future__ import annotations

import hashlib
import math
import os
import secrets
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Callable

import torch
import torchaudio

from comfy_api.latest import io

_REVIEW_EVENT = "h3_exact_audio_lock.audio_review"
_PENDING_ROUTE = "/h3_exact_audio_lock/audio-review/pending"
_DECISION_ROUTE = "/h3_exact_audio_lock/audio-review/decision"
_REVIEW_SUBDIR = "h3_exact_audio_lock_review"
_SUPPORTED_AUDIO_EXTENSIONS = frozenset({".wav", ".mp3", ".flac", ".ogg", ".oga", ".opus"})

_PENDING_REVIEWS: dict[str, dict[str, Any]] = {}
_PENDING_LOCK = threading.Lock()
_ROUTES_REGISTERED = False


def _validate_review_audio(audio: Any) -> tuple[torch.Tensor, int]:
    """Validate the portable ComfyUI AUDIO contract without changing it."""
    if not isinstance(audio, dict):
        raise ValueError("Audio Review / Accept Gate requires a normal ComfyUI AUDIO value.")
    if "waveform" not in audio or "sample_rate" not in audio:
        raise ValueError(
            "Audio Review / Accept Gate requires AUDIO with 'waveform' and 'sample_rate'."
        )

    waveform = audio["waveform"]
    if not isinstance(waveform, torch.Tensor):
        raise ValueError("AUDIO waveform must be a torch.Tensor.")
    if waveform.ndim == 2:
        waveform = waveform.unsqueeze(0)
    if waveform.ndim != 3:
        raise ValueError(
            "AUDIO waveform must have shape [B,C,T] (or [C,T]); "
            f"received {tuple(waveform.shape)}."
        )
    if any(int(size) <= 0 for size in waveform.shape):
        raise ValueError(f"AUDIO waveform contains an empty dimension: {tuple(waveform.shape)}.")
    if not bool(torch.isfinite(waveform).all().item()):
        raise ValueError("AUDIO waveform contains NaN or infinite samples.")

    sample_rate_value = audio["sample_rate"]
    if isinstance(sample_rate_value, bool):
        raise ValueError("AUDIO sample_rate must be a positive integer.")
    try:
        sample_rate = int(sample_rate_value)
        numeric_rate = float(sample_rate_value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("AUDIO sample_rate must be a positive integer.") from exc
    if not math.isfinite(numeric_rate) or numeric_rate != sample_rate or sample_rate <= 0:
        raise ValueError("AUDIO sample_rate must be a positive integer.")

    return waveform, sample_rate


def _input_root() -> Path:
    import folder_paths

    root = Path(folder_paths.get_input_directory()).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _listed_input_audio_files() -> list[str]:
    """List directly selectable managed input files supported by this gate."""
    root = _input_root()
    try:
        names = os.listdir(root)
    except OSError:
        return []
    return sorted(
        name for name in names
        if (root / name).is_file() and Path(name).suffix.lower() in _SUPPORTED_AUDIO_EXTENSIONS
    )


def _resolve_input_audio_file(audio_file: Any) -> Path:
    """Resolve an upload/selection only inside ComfyUI's managed input directory."""
    import folder_paths

    value = str(audio_file or "").strip()
    if not value:
        raise ValueError("audio_file must select or upload an audio file.")
    exists = getattr(folder_paths, "exists_annotated_filepath", None)
    if callable(exists) and not exists(value):
        raise ValueError(f"Audio file does not exist in ComfyUI input: {value!r}.")

    path = Path(folder_paths.get_annotated_filepath(value)).expanduser().resolve()
    root = _input_root()
    if not _is_within(path, root):
        raise ValueError("Audio Review / Accept Gate file input must stay inside ComfyUI's input directory.")
    if not path.is_file():
        raise ValueError(f"Audio file does not exist in ComfyUI input: {value!r}.")
    suffix = path.suffix.lower()
    if suffix not in _SUPPORTED_AUDIO_EXTENSIONS:
        supported = ", ".join(sorted(ext.lstrip(".").upper() for ext in _SUPPORTED_AUDIO_EXTENSIONS))
        raise ValueError(f"Unsupported audio file extension {suffix or '<none>'!r}. Supported: {supported}.")
    return path


def _pcm_to_float32(waveform: torch.Tensor) -> torch.Tensor:
    if waveform.dtype.is_floating_point:
        return waveform.to(torch.float32)
    if waveform.dtype == torch.int16:
        return waveform.to(torch.float32) / float(2 ** 15)
    if waveform.dtype == torch.int32:
        return waveform.to(torch.float32) / float(2 ** 31)
    if waveform.dtype == torch.uint8:
        return (waveform.to(torch.float32) - 128.0) / 128.0
    raise ValueError(f"Unsupported decoded audio sample dtype: {waveform.dtype}.")


def _load_input_audio_file(audio_file: Any) -> tuple[dict[str, Any], Path]:
    """Decode WAV/MP3/FLAC/Ogg/Opus from managed ComfyUI input into AUDIO."""
    path = _resolve_input_audio_file(audio_file)
    try:
        import av
    except ImportError as exc:
        raise RuntimeError(
            "PyAV is required for Audio Review / Accept Gate file input. "
            "Current ComfyUI installations include PyAV for the built-in Load Audio node."
        ) from exc

    try:
        with av.open(str(path)) as container:
            streams = list(container.streams.audio)
            if not streams:
                raise ValueError("No audio stream found in the selected file.")
            stream = streams[0]
            sample_rate = int(stream.codec_context.sample_rate or 0)
            channels = int(stream.channels or 0)
            if sample_rate <= 0 or channels <= 0:
                raise ValueError("Selected audio file reports an invalid sample rate or channel count.")

            frames: list[torch.Tensor] = []
            for frame in container.decode(streams=stream.index):
                buf = torch.from_numpy(frame.to_ndarray())
                if buf.ndim == 1:
                    buf = buf.unsqueeze(0)
                if buf.ndim != 2:
                    raise ValueError(
                        f"Decoded audio frame has unsupported shape {tuple(buf.shape)}."
                    )
                if int(buf.shape[0]) != channels:
                    if int(buf.numel()) % channels != 0:
                        raise ValueError(
                            "Decoded audio frame cannot be reshaped to the reported channel count."
                        )
                    buf = buf.reshape(-1, channels).t()
                frames.append(_pcm_to_float32(buf))
    except ValueError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Could not decode audio file {path.name!r}: {exc}") from exc

    if not frames:
        raise ValueError("No audio frames decoded from the selected file.")
    waveform = torch.cat(frames, dim=1).contiguous().unsqueeze(0)
    audio = {"waveform": waveform, "sample_rate": sample_rate}
    _validate_review_audio(audio)
    return audio, path


def _delete_managed_input_audio_file(path: Path) -> bool:
    """Delete one explicitly selected rejected input file, never an arbitrary path."""
    root = _input_root()
    candidate = Path(path).expanduser().resolve()
    if not _is_within(candidate, root):
        raise RuntimeError("Refusing to delete a rejected file outside ComfyUI's input directory.")
    if candidate.suffix.lower() not in _SUPPORTED_AUDIO_EXTENSIONS:
        raise RuntimeError("Refusing to delete a rejected file with an unsupported extension.")
    if not candidate.exists():
        return False
    if not candidate.is_file():
        raise RuntimeError("Refusing to delete a rejected input path that is not a file.")
    candidate.unlink()
    return True


def _hash_managed_input_audio_file(audio_file: Any) -> str:
    path = _resolve_input_audio_file(audio_file)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _select_review_audio(
    source_mode: str,
    audio: Any = None,
    audio_file: Any = None,
) -> tuple[Any, Path | None]:
    mode = str(source_mode or "").strip().lower()
    if mode == "connected_audio":
        if audio is None:
            raise ValueError(
                "source_mode='connected_audio' requires a connected ComfyUI AUDIO input."
            )
        _validate_review_audio(audio)
        return audio, None
    if mode == "audio_file":
        return _load_input_audio_file(audio_file)
    raise ValueError("source_mode must be 'connected_audio' or 'audio_file'.")


def _preview_batch_item(waveform: torch.Tensor) -> tuple[torch.Tensor, str | None]:
    """Prepare one browser-playable batch item without touching workflow audio."""
    item = waveform.detach().to(device="cpu", dtype=torch.float32).contiguous()
    channels = int(item.shape[0])
    if channels <= 2:
        return item, None

    # Browsers are most reliable with mono/stereo WAV. This affects the temporary
    # review copy only; the AUDIO returned by the node remains byte-for-byte the
    # same tensor object supplied by the upstream node.
    mono = item.mean(dim=0, keepdim=True)
    return mono.repeat(2, 1), f"preview downmixed from {channels} channels to stereo"


def _temp_root() -> Path:
    import folder_paths

    root = Path(folder_paths.get_temp_directory()).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _is_within(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath((str(path), str(root))) == str(root)
    except ValueError:
        return False


def _owned_review_dir(temp_root: Path, token: str) -> Path:
    # token is server-generated, but keep the path rule explicit as defense in depth.
    if not token or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for ch in token):
        raise ValueError("Invalid internal review token.")
    review_root = (temp_root / _REVIEW_SUBDIR).resolve()
    review_dir = review_root / token
    if not _is_within(review_dir, review_root):
        raise RuntimeError("Refusing unsafe review directory path.")
    return review_dir


def _create_review_previews(audio: Any, token: str) -> tuple[list[dict[str, Any]], list[Path], Path, Path]:
    """Create gate-owned temporary WAV files for the browser audio controls."""
    waveform, sample_rate = _validate_review_audio(audio)
    temp_root = _temp_root()
    review_dir = _owned_review_dir(temp_root, token)
    review_dir.mkdir(parents=True, exist_ok=False)

    previews: list[dict[str, Any]] = []
    paths: list[Path] = []
    try:
        for batch_index in range(int(waveform.shape[0])):
            preview_waveform, warning = _preview_batch_item(waveform[batch_index])
            filename = f"batch_{batch_index + 1:03d}.wav"
            path = review_dir / filename
            torchaudio.save(
                str(path),
                preview_waveform,
                sample_rate,
                encoding="PCM_F",
                bits_per_sample=32,
            )
            paths.append(path)
            previews.append(
                {
                    "filename": filename,
                    "subfolder": f"{_REVIEW_SUBDIR}/{token}",
                    "type": "temp",
                    "batch_index": batch_index + 1,
                    "batch_count": int(waveform.shape[0]),
                    "channels": int(preview_waveform.shape[0]),
                    "sample_rate": sample_rate,
                    "samples": int(preview_waveform.shape[-1]),
                    "warning": warning,
                }
            )
    except Exception:
        _cleanup_owned_review_files(paths, review_dir, temp_root)
        try:
            review_dir.rmdir()
        except OSError:
            pass
        raise

    return previews, paths, review_dir, temp_root


def _cleanup_owned_review_files(paths: list[Path], review_dir: Path, temp_root: Path) -> int:
    """Delete only exact files created by this gate inside its own temp directory."""
    temp_root = temp_root.resolve()
    review_root = (temp_root / _REVIEW_SUBDIR).resolve()
    review_dir_resolved = review_dir.resolve()
    if not _is_within(review_dir_resolved, review_root):
        return 0

    removed = 0
    for original in paths:
        path = Path(original)
        # Resolve the parent, not the file itself: unlinking a symlink deletes the
        # link, while following a replaced symlink could point outside our tree.
        try:
            parent = path.parent.resolve()
        except OSError:
            continue
        if parent != review_dir_resolved:
            continue
        try:
            path.unlink(missing_ok=True)
            removed += 1
        except OSError:
            continue

    try:
        review_dir.rmdir()
    except OSError:
        pass
    return removed


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


def _split_audio_source(audio: Any, source_label: str, source_path: Path | None = None) -> list[dict[str, Any]]:
    waveform, sample_rate = _validate_review_audio(audio)
    count = int(waveform.shape[0])
    rows: list[dict[str, Any]] = []
    for batch_index in range(count):
        if count == 1:
            candidate_audio = audio
            label = source_label
        else:
            candidate_audio = {
                "waveform": waveform[batch_index:batch_index + 1],
                "sample_rate": sample_rate,
            }
            label = f"{source_label} / batch {batch_index + 1}"
        rows.append({
            "audio": candidate_audio,
            "label": label,
            "source_path": source_path,
            "source_name": source_path.name if source_path is not None else "",
        })
    return rows


def _collect_review_candidates(
    source_mode: str,
    *,
    audio: Any = None,
    audio_candidates: Any = None,
    audio_file: Any = None,
    audio_files: Any = None,
    runtime_inputs: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Normalize single, batched, and Autogrow sources into candidate rows."""
    runtime_inputs = dict(runtime_inputs or {})
    mode = str(source_mode or "").strip().lower()
    candidates: list[dict[str, Any]] = []

    if mode == "connected_audio":
        sources: list[tuple[str, Any]] = []
        if audio is not None:
            sources.append(("audio", audio))
        sources.extend(_runtime_autogrow_items(
            audio_candidates, runtime_inputs, group_name="audio_candidates"
        ))
        if not sources:
            raise ValueError(
                "source_mode='connected_audio' requires at least one connected ComfyUI AUDIO candidate."
            )
        for source_index, (_socket, value) in enumerate(sources, 1):
            if value is None:
                continue
            candidates.extend(_split_audio_source(value, f"Audio candidate {source_index}"))

    elif mode == "audio_file":
        values: list[Any] = []
        if audio_file not in (None, ""):
            values.append(audio_file)
        values.extend(
            value for _name, value in _runtime_autogrow_items(
                audio_files, runtime_inputs, group_name="audio_files"
            ) if value not in (None, "")
        )
        if not values:
            raise ValueError(
                "source_mode='audio_file' requires at least one selected/uploaded audio file candidate."
            )
        seen_paths: set[Path] = set()
        for value in values:
            decoded, path = _load_input_audio_file(value)
            resolved = path.resolve()
            if resolved in seen_paths:
                raise ValueError(f"Duplicate audio file candidate selected: {path.name!r}.")
            seen_paths.add(resolved)
            candidates.extend(_split_audio_source(decoded, path.name, resolved))
    else:
        raise ValueError("source_mode must be 'connected_audio' or 'audio_file'.")

    if not candidates:
        raise ValueError("Audio Review / Accept Gate received no usable audio candidates.")
    for index, candidate in enumerate(candidates, 1):
        candidate["id"] = f"candidate_{index:03d}"
        candidate["number"] = index
    return candidates


def _create_candidate_previews(
    candidates: list[dict[str, Any]], token: str
) -> tuple[list[dict[str, Any]], list[Path], Path, Path]:
    temp_root = _temp_root()
    review_dir = _owned_review_dir(temp_root, token)
    review_dir.mkdir(parents=True, exist_ok=False)
    previews: list[dict[str, Any]] = []
    paths: list[Path] = []
    try:
        for candidate in candidates:
            waveform, sample_rate = _validate_review_audio(candidate["audio"])
            if int(waveform.shape[0]) != 1:
                raise ValueError("Internal candidate AUDIO must have exactly one batch member.")
            preview_waveform, warning = _preview_batch_item(waveform[0])
            filename = f"{candidate['id']}.wav"
            path = review_dir / filename
            torchaudio.save(
                str(path), preview_waveform, sample_rate,
                encoding="PCM_F", bits_per_sample=32,
            )
            paths.append(path)
            previews.append({
                "candidate_id": candidate["id"],
                "candidate_number": candidate["number"],
                "candidate_count": len(candidates),
                "label": candidate["label"],
                "source_name": candidate.get("source_name", ""),
                "filename": filename,
                "subfolder": f"{_REVIEW_SUBDIR}/{token}",
                "type": "temp",
                "sample_rate": sample_rate,
                "warning": warning,
            })
    except Exception:
        _cleanup_owned_review_files(paths, review_dir, temp_root)
        try:
            review_dir.rmdir()
        except OSError:
            pass
        raise
    return previews, paths, review_dir, temp_root


def _output_root() -> Path:
    import folder_paths

    root = Path(folder_paths.get_output_directory()).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _alternate_output_dir(token: str) -> Path:
    root = _output_root()
    alt_root = (root / "h3_exact_audio_lock_review" / "alternates").resolve()
    alt_dir = (alt_root / token).resolve()
    if not _is_within(alt_dir, alt_root):
        raise RuntimeError("Refusing unsafe alternate output directory path.")
    alt_dir.mkdir(parents=True, exist_ok=True)
    return alt_dir


def _safe_output_stem(value: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "candidate")).strip("._")
    return (stem or "candidate")[:80]


def _save_kept_alternates(
    candidates: list[dict[str, Any]],
    kept_ids: set[str],
    selected_id: str | None,
    token: str,
) -> list[Path]:
    alternate_ids = {candidate_id for candidate_id in kept_ids if candidate_id != selected_id}
    if not alternate_ids:
        return []
    alt_dir = _alternate_output_dir(token)
    saved: list[Path] = []
    candidate_map = {candidate["id"]: candidate for candidate in candidates}
    try:
        for candidate_id in sorted(alternate_ids):
            candidate = candidate_map[candidate_id]
            source_path = candidate.get("source_path")
            if source_path is not None:
                source = Path(source_path).resolve()
                input_root = _input_root()
                if not _is_within(source, input_root) or not source.is_file():
                    raise RuntimeError("Refusing to save an alternate from outside ComfyUI input.")
                suffix = source.suffix.lower()
                if suffix not in _SUPPORTED_AUDIO_EXTENSIONS:
                    raise RuntimeError("Refusing to save an alternate with an unsupported extension.")
                dest = alt_dir / f"{candidate_id}_{_safe_output_stem(source.stem)}{suffix}"
                shutil.copy2(source, dest)
            else:
                waveform, sample_rate = _validate_review_audio(candidate["audio"])
                if int(waveform.shape[0]) != 1:
                    raise ValueError("Alternate candidate AUDIO must have exactly one batch member.")
                dest = alt_dir / f"{candidate_id}.wav"
                torchaudio.save(
                    str(dest),
                    waveform[0].detach().to(device="cpu", dtype=torch.float32).contiguous(),
                    sample_rate,
                    encoding="PCM_F",
                    bits_per_sample=32,
                )
            saved.append(dest)
    except Exception:
        for path in saved:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            alt_dir.rmdir()
        except OSError:
            pass
        raise
    return saved


def _delete_unkept_file_candidates(
    candidates: list[dict[str, Any]], selected_id: str | None, kept_ids: set[str]
) -> list[Path]:
    deleted: list[Path] = []
    protected = set(kept_ids)
    if selected_id:
        protected.add(selected_id)
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate["id"] in protected:
            continue
        source_path = candidate.get("source_path")
        if source_path is None:
            continue
        path = Path(source_path).resolve()
        if path in seen:
            continue
        seen.add(path)
        if _delete_managed_input_audio_file(path):
            deleted.append(path)
    return deleted


def _public_review(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "token": record["token"],
        "label": record["label"],
        "created_at": record["created_at"],
        "deadline": record["deadline"],
        "delete_rejected_audio_files": record["delete_rejected_audio_files"],
        "node_id": record.get("node_id", ""),
        "candidates": record["previews"],
    }


def _list_pending_audio_reviews() -> list[dict[str, Any]]:
    with _PENDING_LOCK:
        return [
            _public_review(record)
            for record in _PENDING_REVIEWS.values()
            if record.get("action") is None
        ]


def _submit_audio_review_decision(
    token: str,
    action: str,
    selected_candidate_id: str | None = None,
    kept_candidate_ids: Any = None,
) -> tuple[bool, str]:
    token = str(token or "")
    action = str(action or "").lower()
    if action not in {"accept", "reject"}:
        return False, "invalid_action"
    kept = kept_candidate_ids if isinstance(kept_candidate_ids, list) else []
    if any(not isinstance(value, str) for value in kept):
        return False, "invalid_kept_candidates"

    with _PENDING_LOCK:
        record = _PENDING_REVIEWS.get(token)
        if record is None:
            return False, "not_found"
        if record.get("action") is not None:
            return False, "already_decided"
        valid_ids = {candidate["id"] for candidate in record["candidates"]}
        kept_set = set(kept)
        if not kept_set.issubset(valid_ids):
            return False, "invalid_kept_candidates"
        selected = str(selected_candidate_id or "")
        if action == "accept" and selected not in valid_ids:
            return False, "invalid_selected_candidate"
        if action == "reject":
            selected = ""
        record["action"] = action
        record["selected_candidate_id"] = selected or None
        record["kept_candidate_ids"] = kept_set
        event = record["event"]

    event.set()
    return True, "ok"


def _notify_review(payload: dict[str, Any]) -> None:
    from server import PromptServer

    PromptServer.instance.send_sync(_REVIEW_EVENT, payload)


def _run_audio_candidate_review(
    candidates: list[dict[str, Any]],
    *,
    review_label: str,
    delete_rejected_audio_files: bool,
    review_timeout_seconds: float,
    node_id: str = "",
    notifier: Callable[[dict[str, Any]], None] | None = None,
) -> Any:
    if not candidates:
        raise ValueError("Audio Review / Accept Gate requires at least one candidate.")
    for candidate in candidates:
        _validate_review_audio(candidate["audio"])
    try:
        timeout = float(review_timeout_seconds)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("review_timeout_seconds must be positive.") from exc
    if not math.isfinite(timeout) or timeout <= 0.0:
        raise ValueError("review_timeout_seconds must be positive and finite.")

    token = secrets.token_urlsafe(18)
    previews, paths, review_dir, temp_root = _create_candidate_previews(candidates, token)
    event = threading.Event()
    now = time.time()
    record: dict[str, Any] = {
        "token": token,
        "label": str(review_label or ""),
        "created_at": now,
        "deadline": now + timeout,
        "delete_rejected_audio_files": bool(delete_rejected_audio_files),
        "node_id": str(node_id or ""),
        "previews": previews,
        "candidates": candidates,
        "paths": paths,
        "review_dir": review_dir,
        "temp_root": temp_root,
        "event": event,
        "action": None,
        "selected_candidate_id": None,
        "kept_candidate_ids": set(),
    }

    with _PENDING_LOCK:
        _PENDING_REVIEWS[token] = record

    try:
        send = notifier or _notify_review
        try:
            send(_public_review(record))
        except Exception as exc:
            _cleanup_owned_review_files(paths, review_dir, temp_root)
            raise RuntimeError(
                "Audio review UI notification failed; review previews were cleaned up."
            ) from exc

        decided = event.wait(timeout)
        action = record.get("action")
        if not decided or action is None:
            _cleanup_owned_review_files(paths, review_dir, temp_root)
            raise TimeoutError(
                "Audio Review / Accept Gate timed out before a review decision."
            )

        selected_id = record.get("selected_candidate_id")
        kept_ids = set(record.get("kept_candidate_ids") or set())
        try:
            saved = _save_kept_alternates(candidates, kept_ids, selected_id, token)

            if bool(delete_rejected_audio_files):
                _delete_unkept_file_candidates(candidates, selected_id, kept_ids)

            if action == "accept":
                for candidate in candidates:
                    if candidate["id"] == selected_id:
                        return candidate["audio"]
                raise RuntimeError("Selected audio candidate disappeared before approval completed.")

            kept_text = f" {len(saved)} alternate choice(s) were saved." if saved else ""
            raise RuntimeError("All audio candidates were rejected by the review gate." + kept_text)
        finally:
            _cleanup_owned_review_files(paths, review_dir, temp_root)
    finally:
        with _PENDING_LOCK:
            current = _PENDING_REVIEWS.get(token)
            if current is record:
                _PENDING_REVIEWS.pop(token, None)


def register_audio_review_routes() -> None:
    """Register same-origin ComfyUI routes once. Called from extension on_load()."""
    global _ROUTES_REGISTERED
    if _ROUTES_REGISTERED:
        return

    from aiohttp import web
    from server import PromptServer

    routes = PromptServer.instance.routes

    @routes.get(_PENDING_ROUTE)
    async def pending_audio_reviews(_request):
        return web.json_response({"reviews": _list_pending_audio_reviews()})

    @routes.post(_DECISION_ROUTE)
    async def decide_audio_review(request):
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid_json"}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"ok": False, "error": "invalid_body"}, status=400)

        ok, reason = _submit_audio_review_decision(
            body.get("token"),
            body.get("action"),
            body.get("selected_candidate_id"),
            body.get("kept_candidate_ids"),
        )
        if ok:
            return web.json_response({"ok": True})
        status = 409 if reason == "already_decided" else 404 if reason == "not_found" else 400
        return web.json_response({"ok": False, "error": reason}, status=status)

    _ROUTES_REGISTERED = True


class AudioReviewAcceptGate(io.ComfyNode):
    """Context-Loop-style candidate review gate for standard ComfyUI AUDIO."""

    @classmethod
    def define_schema(cls) -> io.Schema:
        audio_template = io.Autogrow.TemplatePrefix(
            input=io.Audio.Input("audio_candidate"),
            prefix="audio_candidate_",
            min=0,
        )
        file_template = io.Autogrow.TemplatePrefix(
            input=io.Combo.Input(
                "audio_file_candidate",
                options=_listed_input_audio_files(),
                upload=io.UploadType.audio,
                optional=True,
            ),
            prefix="audio_file_candidate_",
            min=0,
        )
        return io.Schema(
            node_id="H3ExactAudioLockAudioReviewGate",
            display_name="Audio Review / Accept Gate",
            category="MiniMax H3/Audio",
            description=(
                "Review one or many AUDIO/file candidates, keep optional alternates, "
                "and pass only the selected approved AUDIO downstream."
            ),
            inputs=[
                io.Combo.Input(
                    "source_mode",
                    options=["connected_audio", "audio_file"],
                    default="connected_audio",
                    tooltip="Review connected AUDIO candidates or managed ComfyUI input files.",
                ),
                io.Audio.Input(
                    "audio",
                    optional=True,
                    tooltip="First connected AUDIO candidate. Additional candidates use the growing sockets below.",
                ),
                io.Autogrow.Input(
                    "audio_candidates",
                    template=audio_template,
                    optional=True,
                    tooltip="Connect any number of additional TTS/music AUDIO candidates.",
                ),
                io.Combo.Input(
                    "audio_file",
                    options=_listed_input_audio_files(),
                    upload=io.UploadType.audio,
                    optional=True,
                    tooltip="First WAV/MP3/FLAC/OGG/OGA/Opus file candidate from ComfyUI input.",
                ),
                io.Autogrow.Input(
                    "audio_files",
                    template=file_template,
                    optional=True,
                    tooltip="Add any number of additional managed audio-file candidates.",
                ),
                io.String.Input(
                    "review_label",
                    default="",
                    multiline=False,
                    tooltip="Optional label shown above the candidate carousel.",
                ),
                io.Boolean.Input(
                    "delete_rejected_audio_files",
                    default=True,
                    tooltip=(
                        "Delete unselected/unkept managed file candidates after a decision. "
                        "Connected AUDIO has no trusted upstream filepath, so only its temporary preview is removed."
                    ),
                ),
                io.Int.Input(
                    "review_timeout_seconds",
                    default=3600,
                    min=30,
                    max=86400,
                    step=30,
                    tooltip="Maximum time to wait for a browser candidate-review decision.",
                ),
            ],
            outputs=[io.Audio.Output(display_name="accepted_audio")],
            hidden=[io.Hidden.unique_id],
        )

    @classmethod
    def validate_inputs(cls, source_mode: str = "connected_audio", **_kwargs):
        mode = str(source_mode or "").strip().lower()
        if mode not in {"connected_audio", "audio_file"}:
            return "source_mode must be 'connected_audio' or 'audio_file'."
        return True

    @classmethod
    def fingerprint_inputs(
        cls,
        source_mode: str = "connected_audio",
        audio_file=None,
        audio_files=None,
        **runtime_inputs,
    ):
        if str(source_mode or "").strip().lower() != "audio_file":
            return None
        values: list[Any] = []
        if audio_file not in (None, ""):
            values.append(audio_file)
        values.extend(
            value for _name, value in _runtime_autogrow_items(
                audio_files, runtime_inputs, group_name="audio_files"
            ) if value not in (None, "")
        )
        if not values:
            return "no-file-candidates"
        return hashlib.sha256(
            "|".join(_hash_managed_input_audio_file(value) for value in values).encode("utf-8")
        ).hexdigest()

    @classmethod
    def execute(
        cls,
        source_mode: str = "connected_audio",
        audio=None,
        audio_candidates: io.Autogrow.Type | None = None,
        audio_file=None,
        audio_files: io.Autogrow.Type | None = None,
        review_label: str = "",
        delete_rejected_audio_files: bool = True,
        review_timeout_seconds: int = 3600,
        **runtime_inputs,
    ) -> io.NodeOutput:
        candidates = _collect_review_candidates(
            source_mode,
            audio=audio,
            audio_candidates=audio_candidates,
            audio_file=audio_file,
            audio_files=audio_files,
            runtime_inputs=runtime_inputs,
        )
        hidden = getattr(cls, "hidden", None)
        node_id = str(getattr(hidden, "unique_id", "") or "")
        accepted = _run_audio_candidate_review(
            candidates,
            review_label=str(review_label or ""),
            delete_rejected_audio_files=delete_rejected_audio_files,
            review_timeout_seconds=review_timeout_seconds,
            node_id=node_id,
        )
        return io.NodeOutput(accepted)
