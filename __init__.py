# Copyright (c) 2026 Alan Guice (Badgids)
# SPDX-License-Identifier: MIT

"""Exact target-audio locking and sample-accurate multi-track mixing for MiniMax H3.

Public nodes
============
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

VIDEO_FPS = 24
AUDIO_LATENT_FPS = 40
H3_TIMED_AUDIO = io.Custom("H3_TIMED_AUDIO")


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


def _to_stereo(waveform: torch.Tensor) -> torch.Tensor:
    """Normalize Comfy AUDIO waveform [B,C,T] to [1,2,T]."""
    if waveform.ndim == 2:
        waveform = waveform.unsqueeze(0)
    if waveform.ndim != 3:
        raise ValueError(f"Expected AUDIO waveform [B,C,T], got shape {tuple(waveform.shape)}.")
    waveform = waveform[:1].to(torch.float32)
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
    target_samples = max(0, int(target_samples))
    sample_rate = int(sample_rate)
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
        timed_audios: io.Autogrow.Type,
        mix_policy: str,
        overflow_policy: str,
        audio=None,
        legacy_start_frame: int = 0,
    ) -> io.NodeOutput:
        samples = av_latent.get("samples") if isinstance(av_latent, dict) else None
        if samples is None or not getattr(samples, "is_nested", False):
            raise ValueError("MiniMax H3 Exact Audio Lock requires a joint MiniMax H3 AV latent.")
        parts = samples.unbind()
        if len(parts) < 2:
            raise ValueError("MiniMax H3 Exact Audio Lock received an H3 latent without an audio stream.")
        video_latent, target_audio_template = parts[0], parts[1]
        if target_audio_template.ndim < 4:
            raise ValueError("Unexpected MiniMax H3 audio latent shape.")

        vae_rate = int(getattr(audio_vae, "audio_sample_rate", 32000))
        if vae_rate <= 0:
            raise ValueError("MiniMax H3 audio VAE reports an invalid sample rate.")
        target_t = int(target_audio_template.shape[-1])
        target_samples = _target_sample_count(target_t, vae_rate)

        entries: list[dict[str, Any]] = []
        for socket_name, value in _autogrow_items(timed_audios):
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

        encoded = audio_vae.encode(waveform.movedim(1, -1))
        have_t = int(encoded.shape[-1])
        if have_t > target_t:
            encoded = encoded[..., :target_t]
        elif have_t < target_t:
            if have_t <= 0:
                raise ValueError("MiniMax H3 audio VAE returned an empty latent for the target waveform.")
            # Encoder boundary rounding can miss one or two frames. Repeating the
            # final encoded frame avoids injecting arbitrary zero-valued latents.
            tail = encoded[..., -1:].repeat_interleave(target_t - have_t, dim=-1)
            encoded = torch.cat((encoded, tail), dim=-1)

        locked = dict(av_latent)
        locked["samples"] = comfy.nested_tensor.NestedTensor((video_latent, encoded))
        locked["noise_mask"] = comfy.nested_tensor.NestedTensor(
            (torch.ones_like(video_latent), torch.zeros_like(encoded))
        )
        exact_audio = {"waveform": waveform, "sample_rate": vae_rate}
        return io.NodeOutput(locked, exact_audio, json.dumps(manifest, sort_keys=True, separators=(",", ":")))


class H3ExactAudioLockExtension(ComfyExtension):
    @override
    async def get_node_list(self) -> list[type[io.ComfyNode]]:
        return [MiniMaxH3TimedAudio, MiniMaxH3ExactAudioLock]


async def comfy_entrypoint() -> H3ExactAudioLockExtension:
    return H3ExactAudioLockExtension()
