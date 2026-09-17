# Copyright (c) 2026 Alan Guice (Badgids)
# SPDX-License-Identifier: MIT

"""Exact target-audio locking and sample-accurate multi-track mixing for MiniMax H3.

Public nodes
============
Audio Review / Accept Gate
    Context-Loop-style candidate review for connected AUDIO or managed
    WAV/MP3/FLAC/Ogg/Opus files. Select one take, optionally save alternates,
    and pass only the selected AUDIO downstream.

MiniMax H3 Timed Audio
    Wraps one ComfyUI AUDIO value with an exact target start frame and gain.

MiniMax H3 Exact Audio Lock
    Accepts an Autogrow collection of as many Timed Audio inputs as the user
    connects, mixes them deterministically on H3's target timeline, encodes the
    resulting waveform into H3's target audio latent, and freezes audio while
    video remains denoisable.

The mixer works in waveform space before Audio-VAE encoding. Padding is therefore
true digital silence rather than arbitrary zero-valued latent vectors.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any

import torch
import torchaudio

import comfy.nested_tensor
from comfy_api.latest import ComfyExtension, io
from typing_extensions import override

from .audio_review_gate import AudioReviewAcceptGate, register_audio_review_routes

WEB_DIRECTORY = "./web"

VIDEO_FPS = 24
AUDIO_LATENT_FPS = 40
H3_TIMED_AUDIO = io.Custom("H3_TIMED_AUDIO")
H3_SCENE_TIMED_AUDIO = io.Custom("H3_SCENE_TIMED_AUDIO")


def _frame_to_sample(frame_idx: int, sample_rate: int) -> int:
    """Nearest waveform sample to an H3 pixel-frame boundary, deterministically.

    Integer half-up arithmetic avoids float/banker's-rounding differences.
    H3 frame indices are non-negative, so this is exact and deterministic.
    """
    frame_idx = int(frame_idx)
    sample_rate = int(sample_rate)
    if frame_idx < 0:
        raise ValueError("Timed audio start_frame must be non-negative.")
    return (frame_idx * sample_rate + VIDEO_FPS // 2) // VIDEO_FPS


def _target_sample_count(audio_latent_t: int, sample_rate: int) -> int:
    """Waveform samples represented by H3's 40-Hz target audio latent."""
    audio_latent_t = max(0, int(audio_latent_t))
    sample_rate = max(1, int(sample_rate))
    return int(round(audio_latent_t / float(AUDIO_LATENT_FPS) * sample_rate))


def _autogrow_items(group: Any) -> list[tuple[str, Any]]:
    """Return Autogrow values in a stable socket-name order."""
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
    node_name: str = "MiniMaxH3ExactAudioLock",
) -> list[tuple[str, Any]]:
    """Merge normalized and flattened ComfyUI Autogrow runtime inputs.

    ComfyUI's V3 API can expose Autogrow values to ``execute()`` in two forms,
    depending on the runtime/version and execution path:

    * one normalized mapping under ``group_name``; or
    * flattened keyword arguments such as
      ``timed_audios.timed_audio_0``.

    Accept both forms so workflows remain compatible across those runtimes.
    """
    merged = {str(name): value for name, value in _autogrow_items(group)}
    prefix = f"{group_name}."
    unexpected: list[str] = []

    for runtime_name, value in runtime_inputs.items():
        runtime_name = str(runtime_name)
        if not runtime_name.startswith(prefix):
            unexpected.append(runtime_name)
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

    if unexpected:
        names = ", ".join(sorted(unexpected))
        raise TypeError(f"{node_name} received unexpected runtime input(s): {names}")

    return _autogrow_items(merged)


def _to_stereo(waveform: torch.Tensor) -> torch.Tensor:
    """Normalize Comfy AUDIO waveform [B,C,T] to [1,2,T]."""
    if waveform.ndim == 2:
        waveform = waveform.unsqueeze(0)
    if waveform.ndim != 3:
        raise ValueError(f"Expected AUDIO waveform [B,C,T], got shape {tuple(waveform.shape)}.")
    if int(waveform.shape[0]) != 1:
        raise ValueError(
            "Timed Audio accepts exactly one waveform batch; "
            f"received batch size {int(waveform.shape[0])}."
        )
    waveform = waveform.to(torch.float32)
    channels = int(waveform.shape[1])
    if channels == 1:
        return waveform.repeat(1, 2, 1)
    if channels == 2:
        return waveform
    # Deterministic multichannel downmix. H3 target audio is stereo.
    mono = waveform.mean(dim=1, keepdim=True)
    return mono.repeat(1, 2, 1)


def _prepare_track(entry: dict[str, Any], vae_rate: int) -> tuple[torch.Tensor, int, dict[str, Any]]:
    audio = entry.get("audio")
    if not isinstance(audio, dict) or "waveform" not in audio or "sample_rate" not in audio:
        raise ValueError("Timed Audio entry does not contain a valid ComfyUI AUDIO value.")
    frame_idx = int(entry.get("start_frame", 0))
    if frame_idx < 0:
        raise ValueError("Timed Audio start_frame must be non-negative.")
    gain_db = float(entry.get("gain_db", 0.0))
    if not math.isfinite(gain_db):
        raise ValueError("Timed Audio gain_db must be finite.")
    label = str(entry.get("label") or "")

    waveform = _to_stereo(audio["waveform"])
    source_rate = int(audio["sample_rate"])
    if source_rate <= 0:
        raise ValueError("Timed Audio sample_rate must be positive.")
    if source_rate != vae_rate:
        waveform = torchaudio.functional.resample(waveform, source_rate, vae_rate)
    if gain_db != 0.0:
        waveform = waveform * (10.0 ** (gain_db / 20.0))
    if not bool(torch.isfinite(waveform).all().item()):
        raise ValueError("Timed Audio waveform contains NaN or infinite samples.")
    start_sample = _frame_to_sample(frame_idx, vae_rate)
    meta = {
        "label": label,
        "start_frame": frame_idx,
        "start_sample": start_sample,
        "gain_db": gain_db,
        "source_sample_rate": source_rate,
        "mixed_sample_rate": vae_rate,
        "source_samples_after_resample": int(waveform.shape[-1]),
    }
    return waveform, start_sample, meta


def _mix_tracks(
    entries: list[dict[str, Any]],
    *,
    target_samples: int,
    sample_rate: int,
    mix_policy: str,
    overflow_policy: str,
) -> tuple[torch.Tensor, dict[str, Any]]:
    """Deterministically mix timed entries to one exact stereo target waveform."""
    target_samples = int(target_samples)
    sample_rate = int(sample_rate)
    if target_samples <= 0:
        raise ValueError("H3 target audio timeline must contain at least one waveform sample.")
    if sample_rate <= 0:
        raise ValueError("H3 target audio sample rate must be positive.")
    mix_policy = str(mix_policy)
    overflow_policy = str(overflow_policy)
    if mix_policy not in {"sum", "prevent_clipping", "reject_overlap"}:
        raise ValueError(f"Unsupported mix_policy {mix_policy!r}.")
    if overflow_policy not in {"error", "crop"}:
        raise ValueError(f"Unsupported overflow_policy {overflow_policy!r}.")

    prepared: list[tuple[torch.Tensor, int, dict[str, Any]]] = []
    for source_index, entry in enumerate(entries):
        waveform, start_sample, meta = _prepare_track(entry, sample_rate)
        meta["source_index"] = source_index
        prepared.append((waveform, start_sample, meta))

    # Mixing order is fixed independently of connection evaluation order.
    prepared.sort(key=lambda row: (row[1], row[2]["start_frame"], row[2]["label"], row[2]["source_index"]))

    if prepared:
        device = prepared[0][0].device
    else:
        device = torch.device("cpu")
    mixed = torch.zeros((1, 2, target_samples), dtype=torch.float32, device=device)

    intervals: list[tuple[int, int, str]] = []
    track_meta: list[dict[str, Any]] = []
    for waveform, start_sample, meta in prepared:
        if waveform.device != mixed.device:
            waveform = waveform.to(mixed.device)
        original_len = int(waveform.shape[-1])
        if start_sample >= target_samples:
            if overflow_policy == "error":
                raise ValueError(
                    f"Timed audio {meta['label'] or meta['source_index']} starts at sample {start_sample}, "
                    f"outside the target timeline of {target_samples} samples."
                )
            usable = 0
        else:
            usable = min(original_len, target_samples - start_sample)
            if usable < original_len and overflow_policy == "error":
                raise ValueError(
                    f"Timed audio {meta['label'] or meta['source_index']} extends past the H3 target audio timeline "
                    f"({start_sample + original_len} > {target_samples} samples)."
                )

        end_sample = start_sample + usable
        if mix_policy == "reject_overlap" and usable > 0:
            for existing_start, existing_end, existing_label in intervals:
                if start_sample < existing_end and end_sample > existing_start:
                    raise ValueError(
                        f"Timed audio overlap rejected: {meta['label'] or meta['source_index']} "
                        f"[{start_sample},{end_sample}) overlaps {existing_label} "
                        f"[{existing_start},{existing_end})."
                    )
        if usable > 0:
            mixed[..., start_sample:end_sample] += waveform[..., :usable]
            intervals.append((start_sample, end_sample, meta["label"] or str(meta["source_index"])))

        meta = dict(meta)
        meta.update({
            "mixed_samples": usable,
            "end_sample": end_sample,
            "cropped_samples": max(0, original_len - usable),
        })
        track_meta.append(meta)

    peak_before = float(mixed.abs().amax().item()) if mixed.numel() else 0.0
    applied_scale = 1.0
    if mix_policy == "prevent_clipping" and peak_before > 0.999:
        applied_scale = 0.999 / peak_before
        mixed.mul_(applied_scale)
    peak_after = float(mixed.abs().amax().item()) if mixed.numel() else 0.0

    manifest = {
        "version": 2,
        "video_fps": VIDEO_FPS,
        "audio_latent_fps": AUDIO_LATENT_FPS,
        "sample_rate": sample_rate,
        "target_samples": target_samples,
        "track_count": len(track_meta),
        "mix_policy": mix_policy,
        "overflow_policy": overflow_policy,
        "peak_before_policy": peak_before,
        "applied_global_scale": applied_scale,
        "peak_after_policy": peak_after,
        "tracks": track_meta,
    }
    return mixed, manifest


def _validate_h3_av_latent(av_latent: Any, *, node_name: str) -> tuple[torch.Tensor, torch.Tensor]:
    """Validate and return MiniMax H3's video/audio latent streams."""
    samples = av_latent.get("samples") if isinstance(av_latent, dict) else None
    if samples is None or not getattr(samples, "is_nested", False):
        raise ValueError(f"{node_name} requires a joint MiniMax H3 AV latent.")
    parts = samples.unbind()
    if len(parts) != 2:
        raise ValueError(
            f"{node_name} requires exactly two H3 latent streams (video and audio)."
        )
    video_latent, audio_latent = parts
    if video_latent.ndim != 5 or int(video_latent.shape[1]) != 24:
        raise ValueError(
            "Unexpected MiniMax H3 video latent shape; expected [B,24,T,H,W]."
        )
    if (
        audio_latent.ndim != 4
        or int(audio_latent.shape[1]) != 32
        or int(audio_latent.shape[2]) != 2
    ):
        raise ValueError(
            "Unexpected MiniMax H3 audio latent shape; expected [B,32,2,T]."
        )
    if int(video_latent.shape[0]) != 1 or int(audio_latent.shape[0]) != 1:
        raise ValueError(f"{node_name} supports batch size 1 only.")
    return video_latent, audio_latent


def _existing_noise_masks(
    av_latent: dict[str, Any],
    video_latent: torch.Tensor,
    audio_latent: torch.Tensor,
    *,
    node_name: str,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Return upstream masks, or all-denoisable masks when none are present."""
    noise_mask = av_latent.get("noise_mask")
    if noise_mask is None:
        return torch.ones_like(video_latent), torch.ones_like(audio_latent)
    if not getattr(noise_mask, "is_nested", False):
        raise ValueError(f"{node_name} received a non-nested H3 noise_mask.")
    parts = noise_mask.unbind()
    if len(parts) != 2:
        raise ValueError(f"{node_name} requires a two-stream H3 noise_mask.")
    video_mask, audio_mask = parts
    if tuple(video_mask.shape) != tuple(video_latent.shape):
        raise ValueError(f"{node_name} video noise_mask shape does not match the video latent.")
    if tuple(audio_mask.shape) != tuple(audio_latent.shape):
        raise ValueError(f"{node_name} audio noise_mask shape does not match the audio latent.")
    if not bool(torch.isfinite(video_mask).all().item()) or not bool(torch.isfinite(audio_mask).all().item()):
        raise ValueError(f"{node_name} noise_mask contains NaN or infinite values.")
    if bool((video_mask < 0).any().item()) or bool((video_mask > 1).any().item()):
        raise ValueError(f"{node_name} video noise_mask values must be between 0 and 1.")
    if bool((audio_mask < 0).any().item()) or bool((audio_mask > 1).any().item()):
        raise ValueError(f"{node_name} audio noise_mask values must be between 0 and 1.")
    return video_mask, audio_mask


def _encode_target_audio(
    audio_vae: Any,
    waveform: torch.Tensor,
    target_audio_template: torch.Tensor,
) -> torch.Tensor:
    """Encode one target waveform and align encoder boundary rounding to H3 T40."""
    encoded = audio_vae.encode(waveform.movedim(1, -1))
    if (
        not isinstance(encoded, torch.Tensor)
        or encoded.ndim != target_audio_template.ndim
        or tuple(encoded.shape[:-1]) != tuple(target_audio_template.shape[:-1])
    ):
        raise ValueError(
            "MiniMax H3 audio VAE returned an incompatible latent shape; "
            "expected [1,32,2,T]."
        )
    target_t = int(target_audio_template.shape[-1])
    have_t = int(encoded.shape[-1])
    if have_t > target_t:
        encoded = encoded[..., :target_t]
    elif have_t < target_t:
        if have_t <= 0:
            raise ValueError("MiniMax H3 audio VAE returned an empty latent for the target waveform.")
        tail = encoded[..., -1:].repeat_interleave(target_t - have_t, dim=-1)
        encoded = torch.cat((encoded, tail), dim=-1)
    if not bool(torch.isfinite(encoded).all().item()):
        raise ValueError("MiniMax H3 audio VAE returned NaN or infinite latent values.")
    return encoded


def _validate_nonnegative_ms(value: Any, *, field_name: str, maximum: int = 5000) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer number of milliseconds.")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer number of milliseconds.") from exc
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f"{field_name} must be an integer number of milliseconds.")
    if result < 0 or result > maximum:
        raise ValueError(f"{field_name} must be between 0 and {maximum} ms.")
    return result


def _ms_to_audio_frames(milliseconds: int) -> int:
    """Round a millisecond safety interval up to H3's 40-Hz audio grid."""
    return (int(milliseconds) * AUDIO_LATENT_FPS + 999) // 1000


def _sample_interval_to_audio_frames(start_sample: int, end_sample: int, sample_rate: int) -> tuple[int, int]:
    """Conservatively cover a waveform interval on H3's 40-Hz latent grid."""
    start = max(0, int(start_sample))
    end = max(start, int(end_sample))
    sr = int(sample_rate)
    if sr <= 0:
        raise ValueError("H3 target audio sample rate must be positive.")
    start_frame = (start * AUDIO_LATENT_FPS) // sr
    end_frame = (end * AUDIO_LATENT_FPS + sr - 1) // sr
    return start_frame, end_frame


def _dialogue_noise_mask(
    audio_template: torch.Tensor,
    tracks: list[dict[str, Any]],
    *,
    sample_rate: int,
    protect_before_ms: int,
    protect_after_ms: int,
    feather_ms: int,
) -> tuple[torch.Tensor, list[dict[str, int]]]:
    """Build 1=generate / 0=protect mask with exact cores and optional soft edges."""
    before = _ms_to_audio_frames(protect_before_ms)
    after = _ms_to_audio_frames(protect_after_ms)
    feather = _ms_to_audio_frames(feather_ms)
    target_t = int(audio_template.shape[-1])
    temporal = torch.ones((target_t,), dtype=audio_template.dtype, device=audio_template.device)
    protected: list[dict[str, int]] = []

    for track in tracks:
        mixed_samples = int(track.get("mixed_samples", 0))
        if mixed_samples <= 0:
            continue
        core_start, core_end = _sample_interval_to_audio_frames(
            int(track["start_sample"]), int(track["end_sample"]), sample_rate
        )
        core_start = min(target_t, core_start)
        core_end = min(target_t, max(core_start, core_end))
        hard_start = max(0, core_start - before)
        hard_end = min(target_t, core_end + after)
        if hard_end > hard_start:
            temporal[hard_start:hard_end] = 0

        left_start = max(0, hard_start - feather)
        left_count = hard_start - left_start
        if left_count > 0:
            ramp = torch.arange(
                left_count, 0, -1, dtype=audio_template.dtype, device=audio_template.device
            ) / float(left_count + 1)
            temporal[left_start:hard_start] = torch.minimum(
                temporal[left_start:hard_start], ramp
            )

        right_end = min(target_t, hard_end + feather)
        right_count = right_end - hard_end
        if right_count > 0:
            ramp = torch.arange(
                1, right_count + 1, dtype=audio_template.dtype, device=audio_template.device
            ) / float(right_count + 1)
            temporal[hard_end:right_end] = torch.minimum(
                temporal[hard_end:right_end], ramp
            )

        protected.append({
            "core_start_audio_frame": core_start,
            "core_end_audio_frame": core_end,
            "hard_start_audio_frame": hard_start,
            "hard_end_audio_frame": hard_end,
            "feather_start_audio_frame": left_start,
            "feather_end_audio_frame": right_end,
        })

    mask = temporal.reshape(1, 1, 1, target_t).expand_as(audio_template).clone()
    return mask, protected


class MiniMaxH3TimedAudio(io.ComfyNode):
    """One audio utterance/event plus its exact H3 target-frame placement."""

    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="MiniMaxH3TimedAudio",
            display_name="MiniMax H3 Timed Audio",
            category="MiniMax H3/Audio",
            description=(
                "Wrap one AUDIO source with its exact MiniMax H3 target start frame. "
                "Connect any number of these to MiniMax H3 Exact Audio Lock."
            ),
            inputs=[
                io.Audio.Input("audio", tooltip="One dialogue/singing/audio event."),
                io.Int.Input(
                    "start_frame", default=0, min=0, max=1_000_000, step=1,
                    tooltip="Pixel-frame index on H3's 24 fps target video timeline.",
                ),
                io.Float.Input(
                    "gain_db", default=0.0, min=-60.0, max=24.0, step=0.1,
                    tooltip="Gain applied to this source before mixing. 0 dB preserves its level.",
                ),
                io.String.Input(
                    "label", default="", multiline=False,
                    tooltip="Optional speaker/event label used only in the mix manifest.",
                ),
            ],
            outputs=[H3_TIMED_AUDIO.Output(display_name="timed_audio")],
        )

    @classmethod
    def execute(cls, audio, start_frame: int, gain_db: float, label: str) -> io.NodeOutput:
        return io.NodeOutput({
            "audio": audio,
            "start_frame": int(start_frame),
            "gain_db": float(gain_db),
            "label": str(label or ""),
        })


class MiniMaxH3ExactAudioLock(io.ComfyNode):
    """Mix unlimited timed AUDIO events into one locked MiniMax H3 target stream."""

    @classmethod
    def define_schema(cls) -> io.Schema:
        timed_template = io.Autogrow.TemplatePrefix(
            input=H3_TIMED_AUDIO.Input("timed_audio"),
            prefix="timed_audio",
            min=0,
            # Intentionally no max: ComfyUI keeps growing sockets as the user
            # connects them. Practical limits are only graph/system resources.
        )
        return io.Schema(
            node_id="MiniMaxH3ExactAudioLock",
            display_name="MiniMax H3 Exact Audio Lock",
            category="MiniMax H3/Audio",
            description=(
                "Deterministically mixes any number of timed audio events on H3's exact target timeline, "
                "encodes that one waveform into the target audio latent, freezes audio, and denoises video only."
            ),
            inputs=[
                io.Latent.Input("av_latent", tooltip="Joint MiniMax H3 video/audio target latent."),
                io.Vae.Input("audio_vae", tooltip="MiniMax H3 audio VAE."),
                io.Autogrow.Input(
                    "timed_audios",
                    template=timed_template,
                    tooltip="Connect as many MiniMax H3 Timed Audio nodes as needed. There is no node-level maximum.",
                ),
                io.Combo.Input(
                    "mix_policy", options=["sum", "prevent_clipping", "reject_overlap"], default="sum",
                    tooltip=(
                        "sum = exact arithmetic sum; prevent_clipping = scale the complete bus only when peak > 0.999; "
                        "reject_overlap = error if timed source intervals overlap."
                    ),
                ),
                io.Combo.Input(
                    "overflow_policy", options=["error", "crop"], default="error",
                    tooltip="What to do when a source extends beyond the H3 target timeline.",
                ),
                # Backward compatibility for v1 workflows. If connected, this is
                # treated as one additional source at legacy_start_frame.
                io.Audio.Input(
                    "audio", optional=True,
                    tooltip="Legacy single AUDIO input. Prefer Timed Audio inputs for new workflows.",
                ),
                io.Int.Input(
                    "legacy_start_frame", default=0, min=0, max=1_000_000, step=1,
                    tooltip="Start frame for the optional legacy single AUDIO input.",
                ),
            ],
            outputs=[
                io.Latent.Output(display_name="locked_av_latent"),
                io.Audio.Output(display_name="exact_audio"),
                io.String.Output(display_name="mix_manifest"),
            ],
        )

    @classmethod
    def execute(
        cls,
        av_latent,
        audio_vae,
        timed_audios: io.Autogrow.Type | None = None,
        mix_policy: str = "sum",
        overflow_policy: str = "error",
        audio=None,
        legacy_start_frame: int = 0,
        **runtime_inputs,
    ) -> io.NodeOutput:
        video_latent, target_audio_template = _validate_h3_av_latent(
            av_latent, node_name="MiniMax H3 Exact Audio Lock"
        )
        video_mask, _ = _existing_noise_masks(
            av_latent, video_latent, target_audio_template,
            node_name="MiniMax H3 Exact Audio Lock",
        )

        vae_rate = int(getattr(audio_vae, "audio_sample_rate", 32000))
        if vae_rate <= 0:
            raise ValueError("MiniMax H3 audio VAE reports an invalid sample rate.")
        target_t = int(target_audio_template.shape[-1])
        target_samples = _target_sample_count(target_t, vae_rate)

        entries: list[dict[str, Any]] = []
        timed_audio_items = _runtime_autogrow_items(
            timed_audios,
            runtime_inputs,
            group_name="timed_audios",
        )
        for socket_name, value in timed_audio_items:
            if value is None:
                continue
            if not isinstance(value, dict):
                raise ValueError(f"{socket_name} is not a MiniMax H3 Timed Audio value.")
            row = dict(value)
            row.setdefault("label", socket_name)
            entries.append(row)
        if audio is not None:
            entries.append({
                "audio": audio,
                "start_frame": int(legacy_start_frame),
                "gain_db": 0.0,
                "label": "legacy_audio",
            })

        waveform, manifest = _mix_tracks(
            entries,
            target_samples=target_samples,
            sample_rate=vae_rate,
            mix_policy=mix_policy,
            overflow_policy=overflow_policy,
        )
        manifest["target_audio_latent_frames"] = target_t

        encoded = _encode_target_audio(audio_vae, waveform, target_audio_template)

        locked = dict(av_latent)
        locked["samples"] = comfy.nested_tensor.NestedTensor((video_latent, encoded))
        locked["noise_mask"] = comfy.nested_tensor.NestedTensor(
            (video_mask, torch.zeros_like(encoded))
        )
        exact_audio = {"waveform": waveform, "sample_rate": vae_rate}
        return io.NodeOutput(locked, exact_audio, json.dumps(manifest, sort_keys=True, separators=(",", ":")))


class MiniMaxH3DialogueAudioLock(io.ComfyNode):
    """Protect timed dialogue while leaving the rest of H3 audio generative."""

    @classmethod
    def define_schema(cls) -> io.Schema:
        timed_template = io.Autogrow.TemplatePrefix(
            input=H3_TIMED_AUDIO.Input("timed_audio"), prefix="timed_audio", min=0
        )
        return io.Schema(
            node_id="MiniMaxH3DialogueAudioLock",
            display_name="MiniMax H3 Dialogue Audio Lock",
            category="MiniMax H3/Audio",
            description=(
                "Protect only supplied dialogue intervals in H3's target audio latent. "
                "Unprotected intervals remain denoisable so H3 can generate ambience, "
                "room tone, music, footsteps, effects, and other scene audio."
            ),
            inputs=[
                io.Latent.Input("av_latent"),
                io.Vae.Input("audio_vae"),
                io.Autogrow.Input("timed_audios", template=timed_template),
                io.Combo.Input(
                    "mix_policy", options=["sum", "prevent_clipping", "reject_overlap"],
                    default="prevent_clipping",
                ),
                io.Combo.Input("overflow_policy", options=["error", "crop"], default="error"),
                io.Int.Input(
                    "protect_before_ms", default=200, min=0, max=5000, step=25,
                    tooltip="Hard-protected margin before each supplied dialogue event.",
                ),
                io.Int.Input(
                    "protect_after_ms", default=250, min=0, max=5000, step=25,
                    tooltip="Hard-protected margin after each supplied dialogue event.",
                ),
                io.Int.Input(
                    "feather_ms", default=100, min=0, max=5000, step=25,
                    tooltip="Soft transition outside hard-protected margins. Dialogue core stays mask=0.",
                ),
            ],
            outputs=[
                io.Latent.Output(display_name="dialogue_locked_av_latent"),
                io.Audio.Output(display_name="dialogue_reference_audio"),
                io.String.Output(display_name="dialogue_lock_manifest"),
            ],
        )

    @classmethod
    def execute(
        cls,
        av_latent,
        audio_vae,
        timed_audios: io.Autogrow.Type | None = None,
        mix_policy: str = "prevent_clipping",
        overflow_policy: str = "error",
        protect_before_ms: int = 200,
        protect_after_ms: int = 250,
        feather_ms: int = 100,
        **runtime_inputs,
    ) -> io.NodeOutput:
        before_ms = _validate_nonnegative_ms(protect_before_ms, field_name="protect_before_ms")
        after_ms = _validate_nonnegative_ms(protect_after_ms, field_name="protect_after_ms")
        feather = _validate_nonnegative_ms(feather_ms, field_name="feather_ms")
        video_latent, target_audio_template = _validate_h3_av_latent(
            av_latent, node_name="MiniMax H3 Dialogue Audio Lock"
        )
        video_mask, upstream_audio_mask = _existing_noise_masks(
            av_latent, video_latent, target_audio_template,
            node_name="MiniMax H3 Dialogue Audio Lock",
        )
        vae_rate = int(getattr(audio_vae, "audio_sample_rate", 32000))
        if vae_rate <= 0:
            raise ValueError("MiniMax H3 audio VAE reports an invalid sample rate.")
        target_t = int(target_audio_template.shape[-1])
        target_samples = _target_sample_count(target_t, vae_rate)

        entries: list[dict[str, Any]] = []
        for socket_name, value in _runtime_autogrow_items(
            timed_audios, runtime_inputs, group_name="timed_audios",
            node_name="MiniMaxH3DialogueAudioLock",
        ):
            if value is None:
                continue
            if not isinstance(value, dict):
                raise ValueError(f"{socket_name} is not a MiniMax H3 Timed Audio value.")
            row = dict(value)
            row.setdefault("label", socket_name)
            entries.append(row)
        if not entries:
            raise ValueError("MiniMax H3 Dialogue Audio Lock requires at least one Timed Audio event.")

        waveform, manifest = _mix_tracks(
            entries, target_samples=target_samples, sample_rate=vae_rate,
            mix_policy=mix_policy, overflow_policy=overflow_policy,
        )
        encoded = _encode_target_audio(audio_vae, waveform, target_audio_template)
        dialogue_mask, protection = _dialogue_noise_mask(
            encoded, manifest["tracks"], sample_rate=vae_rate,
            protect_before_ms=before_ms, protect_after_ms=after_ms, feather_ms=feather,
        )
        upstream_audio_mask = upstream_audio_mask.to(dialogue_mask.dtype)
        # Only the dialogue/feather region may replace the incoming target audio.
        # This preserves Context Loop audio carried in fully generative regions.
        dialogue_weight = 1.0 - dialogue_mask
        partial_audio = (
            target_audio_template.to(encoded.dtype) * dialogue_mask
            + encoded * dialogue_weight
        )
        combined_audio_mask = torch.minimum(upstream_audio_mask, dialogue_mask)

        partial = dict(av_latent)
        partial["samples"] = comfy.nested_tensor.NestedTensor((video_latent, partial_audio))
        partial["noise_mask"] = comfy.nested_tensor.NestedTensor((video_mask, combined_audio_mask))
        manifest.update({
            "mode": "dialogue_partial_lock",
            "target_audio_latent_frames": target_t,
            "protect_before_ms": before_ms,
            "protect_after_ms": after_ms,
            "feather_ms": feather,
            "protection": protection,
            "reference_audio_is_final_mix": False,
        })
        reference_audio = {"waveform": waveform, "sample_rate": vae_rate}
        return io.NodeOutput(
            partial,
            reference_audio,
            json.dumps(manifest, sort_keys=True, separators=(",", ":")),
        )


def _validate_scene_index(value: Any, *, field_name: str) -> int:
    """Validate the one-based scene index used by recursive H3 workflows."""
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a one-based integer scene index.")
    try:
        scene_index = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a one-based integer scene index.") from exc
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f"{field_name} must be a one-based integer scene index.")
    if scene_index < 1:
        raise ValueError(f"{field_name} must be at least 1.")
    return scene_index


class MiniMaxH3SceneTimedAudio(io.ComfyNode):
    """One audio event assigned to one recursive H3 scene."""

    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="MiniMaxH3SceneTimedAudio",
            display_name="MiniMax H3 Scene Timed Audio",
            category="MiniMax H3/Audio",
            description=(
                "Assign one AUDIO event to one one-based scene and one scene-local "
                "24 fps start frame. Designed for recursive H3 chain workflows."
            ),
            inputs=[
                io.Audio.Input("audio", tooltip="One approved dialogue/singing/audio event."),
                io.Int.Input(
                    "scene_index", default=1, min=1, max=1_000_000, step=1,
                    tooltip="One-based scene number. H3 Context Loop clip_index is one-based.",
                ),
                io.Int.Input(
                    "start_frame", default=0, min=0, max=1_000_000, step=1,
                    tooltip="Scene-local pixel-frame index on H3's 24 fps target timeline.",
                ),
                io.Float.Input(
                    "gain_db", default=0.0, min=-60.0, max=24.0, step=0.1,
                    tooltip="Gain applied before scene mixing. 0 dB preserves source level.",
                ),
                io.String.Input(
                    "label", default="", multiline=False,
                    tooltip="Optional speaker/event label written to the mix manifest.",
                ),
            ],
            outputs=[H3_SCENE_TIMED_AUDIO.Output(display_name="scene_timed_audio")],
        )

    @classmethod
    def execute(
        cls,
        audio,
        scene_index: int,
        start_frame: int,
        gain_db: float,
        label: str,
    ) -> io.NodeOutput:
        return io.NodeOutput({
            "audio": audio,
            "scene_index": _validate_scene_index(scene_index, field_name="scene_index"),
            "start_frame": int(start_frame),
            "gain_db": float(gain_db),
            "label": str(label or ""),
        })


class MiniMaxH3SceneExactAudioLock(io.ComfyNode):
    """Select this recursive scene's events and delegate to the exact lock."""

    @classmethod
    def define_schema(cls) -> io.Schema:
        scene_template = io.Autogrow.TemplatePrefix(
            input=H3_SCENE_TIMED_AUDIO.Input("scene_timed_audio"),
            prefix="scene_timed_audio",
            min=0,
        )
        return io.Schema(
            node_id="MiniMaxH3SceneExactAudioLock",
            display_name="MiniMax H3 Scene Exact Audio Lock",
            category="MiniMax H3/Audio",
            description=(
                "For recursive H3 workflows: select only the events assigned to "
                "current_scene, then apply the normal exact target-audio lock."
            ),
            inputs=[
                io.Latent.Input("av_latent", tooltip="Joint MiniMax H3 video/audio target latent."),
                io.Vae.Input("audio_vae", tooltip="MiniMax H3 audio VAE."),
                io.Int.Input(
                    "current_scene", default=1, min=1, max=1_000_000, step=1,
                    tooltip=(
                        "One-based current scene. Connect MiniMax H3 Chain Current "
                        "clip_index here."
                    ),
                ),
                io.Autogrow.Input(
                    "scene_timed_audios",
                    template=scene_template,
                    tooltip=(
                        "Connect Scene Timed Audio events from every scene. Only the "
                        "current scene is mixed during this recursive iteration."
                    ),
                ),
                io.Combo.Input(
                    "mix_policy",
                    options=["sum", "prevent_clipping", "reject_overlap"],
                    default="sum",
                ),
                io.Combo.Input(
                    "overflow_policy", options=["error", "crop"], default="error",
                ),
                io.Combo.Input(
                    "empty_scene_policy",
                    options=["error", "lock_silence"],
                    default="error",
                    tooltip=(
                        "error catches missing scene audio. lock_silence deliberately "
                        "locks a digital-silence target for scenes with no events."
                    ),
                ),
            ],
            outputs=[
                io.Latent.Output(display_name="locked_av_latent"),
                io.Audio.Output(display_name="exact_audio"),
                io.String.Output(display_name="mix_manifest"),
            ],
        )

    @classmethod
    def execute(
        cls,
        av_latent,
        audio_vae,
        current_scene: int,
        scene_timed_audios: io.Autogrow.Type | None = None,
        mix_policy: str = "sum",
        overflow_policy: str = "error",
        empty_scene_policy: str = "error",
        **runtime_inputs,
    ) -> io.NodeOutput:
        scene_index = _validate_scene_index(current_scene, field_name="current_scene")
        if empty_scene_policy not in {"error", "lock_silence"}:
            raise ValueError(
                "empty_scene_policy must be 'error' or 'lock_silence'."
            )

        scene_items = _runtime_autogrow_items(
            scene_timed_audios,
            runtime_inputs,
            group_name="scene_timed_audios",
            node_name="MiniMaxH3SceneExactAudioLock",
        )
        selected: dict[str, dict[str, Any]] = {}
        for socket_name, value in scene_items:
            if value is None:
                continue
            if not isinstance(value, dict):
                raise ValueError(
                    f"{socket_name} is not a MiniMax H3 Scene Timed Audio value."
                )
            event_scene = _validate_scene_index(
                value.get("scene_index"), field_name=f"{socket_name}.scene_index"
            )
            if event_scene != scene_index:
                continue
            row = dict(value)
            row.pop("scene_index", None)
            row.setdefault("label", socket_name)
            selected[f"timed_audio_{len(selected)}"] = row

        if not selected and empty_scene_policy == "error":
            raise ValueError(
                f"No Scene Timed Audio events are assigned to scene {scene_index}. "
                "Add an event or explicitly choose empty_scene_policy='lock_silence'."
            )

        return MiniMaxH3ExactAudioLock.execute(
            av_latent,
            audio_vae,
            timed_audios=selected,
            mix_policy=mix_policy,
            overflow_policy=overflow_policy,
        )


class MiniMaxH3SceneDialogueAudioLock(io.ComfyNode):
    """Apply partial dialogue protection only to the current recursive scene."""

    @classmethod
    def define_schema(cls) -> io.Schema:
        scene_template = io.Autogrow.TemplatePrefix(
            input=H3_SCENE_TIMED_AUDIO.Input("scene_timed_audio"),
            prefix="scene_timed_audio", min=0,
        )
        return io.Schema(
            node_id="MiniMaxH3SceneDialogueAudioLock",
            display_name="MiniMax H3 Scene Dialogue Audio Lock",
            category="MiniMax H3/Audio",
            description=(
                "Pippa-style recursive film audio: protect supplied dialogue for the "
                "current scene while H3 generates all unprotected scene audio."
            ),
            inputs=[
                io.Latent.Input("av_latent"),
                io.Vae.Input("audio_vae"),
                io.Int.Input("current_scene", default=1, min=1, max=1_000_000, step=1),
                io.Autogrow.Input("scene_timed_audios", template=scene_template),
                io.Combo.Input(
                    "mix_policy", options=["sum", "prevent_clipping", "reject_overlap"],
                    default="prevent_clipping",
                ),
                io.Combo.Input("overflow_policy", options=["error", "crop"], default="error"),
                io.Combo.Input(
                    "empty_scene_policy", options=["generate", "error"], default="generate",
                    tooltip="generate leaves scenes without dialogue fully generative; error catches missing schedules.",
                ),
                io.Int.Input("protect_before_ms", default=200, min=0, max=5000, step=25),
                io.Int.Input("protect_after_ms", default=250, min=0, max=5000, step=25),
                io.Int.Input("feather_ms", default=100, min=0, max=5000, step=25),
            ],
            outputs=[
                io.Latent.Output(display_name="dialogue_locked_av_latent"),
                io.Audio.Output(display_name="dialogue_reference_audio"),
                io.String.Output(display_name="dialogue_lock_manifest"),
            ],
        )

    @classmethod
    def execute(
        cls,
        av_latent,
        audio_vae,
        current_scene: int,
        scene_timed_audios: io.Autogrow.Type | None = None,
        mix_policy: str = "prevent_clipping",
        overflow_policy: str = "error",
        empty_scene_policy: str = "generate",
        protect_before_ms: int = 200,
        protect_after_ms: int = 250,
        feather_ms: int = 100,
        **runtime_inputs,
    ) -> io.NodeOutput:
        scene_index = _validate_scene_index(current_scene, field_name="current_scene")
        if empty_scene_policy not in {"generate", "error"}:
            raise ValueError("empty_scene_policy must be 'generate' or 'error'.")
        scene_items = _runtime_autogrow_items(
            scene_timed_audios, runtime_inputs, group_name="scene_timed_audios",
            node_name="MiniMaxH3SceneDialogueAudioLock",
        )
        selected: dict[str, dict[str, Any]] = {}
        for socket_name, value in scene_items:
            if value is None:
                continue
            if not isinstance(value, dict):
                raise ValueError(f"{socket_name} is not a MiniMax H3 Scene Timed Audio value.")
            event_scene = _validate_scene_index(
                value.get("scene_index"), field_name=f"{socket_name}.scene_index"
            )
            if event_scene != scene_index:
                continue
            row = dict(value)
            row.pop("scene_index", None)
            row.setdefault("label", socket_name)
            selected[f"timed_audio_{len(selected)}"] = row

        if not selected:
            if empty_scene_policy == "error":
                raise ValueError(f"No Scene Timed Audio events are assigned to scene {scene_index}.")
            video_latent, audio_template = _validate_h3_av_latent(
                av_latent, node_name="MiniMax H3 Scene Dialogue Audio Lock"
            )
            vae_rate = int(getattr(audio_vae, "audio_sample_rate", 32000))
            if vae_rate <= 0:
                raise ValueError("MiniMax H3 audio VAE reports an invalid sample rate.")
            target_samples = _target_sample_count(int(audio_template.shape[-1]), vae_rate)
            silence = torch.zeros(
                (1, 2, target_samples), dtype=torch.float32, device=audio_template.device
            )
            manifest = {
                "version": 3,
                "mode": "dialogue_partial_lock",
                "scene_index": scene_index,
                "track_count": 0,
                "empty_scene_policy": "generate",
                "reference_audio_is_final_mix": False,
            }
            return io.NodeOutput(
                dict(av_latent),
                {"waveform": silence, "sample_rate": vae_rate},
                json.dumps(manifest, sort_keys=True, separators=(",", ":")),
            )

        output = MiniMaxH3DialogueAudioLock.execute(
            av_latent, audio_vae, timed_audios=selected,
            mix_policy=mix_policy, overflow_policy=overflow_policy,
            protect_before_ms=protect_before_ms, protect_after_ms=protect_after_ms,
            feather_ms=feather_ms,
        )
        latent, reference_audio, manifest_json = output.values
        manifest = json.loads(manifest_json)
        manifest["scene_index"] = scene_index
        manifest["empty_scene_policy"] = empty_scene_policy
        return io.NodeOutput(
            latent, reference_audio,
            json.dumps(manifest, sort_keys=True, separators=(",", ":")),
        )


class H3ExactAudioLockExtension(ComfyExtension):
    @override
    async def on_load(self) -> None:
        register_audio_review_routes()

    @override
    async def get_node_list(self) -> list[type[io.ComfyNode]]:
        return [
            MiniMaxH3TimedAudio,
            MiniMaxH3ExactAudioLock,
            MiniMaxH3DialogueAudioLock,
            MiniMaxH3SceneTimedAudio,
            MiniMaxH3SceneExactAudioLock,
            MiniMaxH3SceneDialogueAudioLock,
            AudioReviewAcceptGate,
        ]


async def comfy_entrypoint() -> H3ExactAudioLockExtension:
    return H3ExactAudioLockExtension()
