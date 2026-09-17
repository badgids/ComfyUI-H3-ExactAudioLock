# Copyright (c) 2026 Alan Guice (Badgids)
# SPDX-License-Identifier: MIT

"""Post-sampling dialogue restoration for MiniMax H3 partial audio locks."""
from __future__ import annotations

import json
from typing import Any

import torch
import torchaudio
from comfy_api.latest import io


def _to_stereo(waveform: torch.Tensor) -> torch.Tensor:
    if waveform.ndim == 2:
        waveform = waveform.unsqueeze(0)
    if waveform.ndim != 3:
        raise ValueError(f"Expected AUDIO waveform [B,C,T], got shape {tuple(waveform.shape)}.")
    if int(waveform.shape[0]) != 1:
        raise ValueError(
            "Dialogue Audio Finalize accepts exactly one waveform batch; "
            f"received batch size {int(waveform.shape[0])}."
        )
    waveform = waveform.to(torch.float32)
    channels = int(waveform.shape[1])
    if channels == 1:
        return waveform.repeat(1, 2, 1)
    if channels == 2:
        return waveform
    mono = waveform.mean(dim=1, keepdim=True)
    return mono.repeat(1, 2, 1)


def _audio_value_at_rate(audio: Any, target_rate: int, *, field_name: str) -> torch.Tensor:
    if not isinstance(audio, dict) or "waveform" not in audio or "sample_rate" not in audio:
        raise ValueError(f"{field_name} is not a valid ComfyUI AUDIO value.")
    source_rate = int(audio["sample_rate"])
    if source_rate <= 0:
        raise ValueError(f"{field_name} sample_rate must be positive.")
    waveform = _to_stereo(audio["waveform"])
    if source_rate != target_rate:
        waveform = torchaudio.functional.resample(waveform, source_rate, target_rate)
    if not bool(torch.isfinite(waveform).all().item()):
        raise ValueError(f"{field_name} contains NaN or infinite samples.")
    return waveform


class MiniMaxH3DialogueAudioFinalize(io.ComfyNode):
    """Restore deterministic dialogue cores after H3 generates unsupplied audio."""

    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="MiniMaxH3DialogueAudioFinalize",
            display_name="MiniMax H3 Dialogue Audio Finalize",
            category="MiniMax H3/Audio",
            description=(
                "Post-sampling finalizer for dialogue-partial locking. Preserves H3-generated "
                "audio outside supplied dialogue and restores the supplied dialogue waveform "
                "sample-for-sample inside each exact scheduled core interval."
            ),
            inputs=[
                io.Audio.Input("generated_audio", tooltip="Audio decoded from the sampled H3 AV latent."),
                io.Audio.Input(
                    "dialogue_reference_audio",
                    tooltip="Deterministic full-length dialogue reference from a Dialogue Audio Lock node.",
                ),
                io.String.Input(
                    "dialogue_lock_manifest",
                    multiline=True,
                    tooltip="Manifest from the matching Dialogue Audio Lock node.",
                ),
            ],
            outputs=[io.Audio.Output(display_name="final_audio")],
        )

    @classmethod
    def execute(
        cls,
        generated_audio,
        dialogue_reference_audio,
        dialogue_lock_manifest: str,
    ) -> io.NodeOutput:
        reference_rate = (
            int(dialogue_reference_audio.get("sample_rate", 0))
            if isinstance(dialogue_reference_audio, dict)
            else 0
        )
        if reference_rate <= 0:
            raise ValueError("dialogue_reference_audio sample_rate must be positive.")

        reference = _audio_value_at_rate(
            dialogue_reference_audio,
            reference_rate,
            field_name="dialogue_reference_audio",
        )
        generated = _audio_value_at_rate(
            generated_audio,
            reference_rate,
            field_name="generated_audio",
        )
        if generated.device != reference.device:
            generated = generated.to(reference.device)
        target_samples = int(reference.shape[-1])
        if target_samples <= 0:
            raise ValueError("dialogue_reference_audio must contain at least one sample.")

        if int(generated.shape[-1]) > target_samples:
            generated = generated[..., :target_samples]
        elif int(generated.shape[-1]) < target_samples:
            generated = torch.nn.functional.pad(
                generated,
                (0, target_samples - int(generated.shape[-1])),
            )

        try:
            manifest = json.loads(str(dialogue_lock_manifest))
        except (TypeError, ValueError) as exc:
            raise ValueError("dialogue_lock_manifest is not valid JSON.") from exc
        if not isinstance(manifest, dict) or manifest.get("mode") != "dialogue_partial_lock":
            raise ValueError("dialogue_lock_manifest is not a dialogue_partial_lock manifest.")
        tracks = manifest.get("tracks", [])
        if not isinstance(tracks, list):
            raise ValueError("dialogue_lock_manifest tracks must be a list.")

        final = generated.clone()
        for index, track in enumerate(tracks):
            if not isinstance(track, dict):
                raise ValueError(f"dialogue_lock_manifest track {index} is not an object.")
            mixed_samples = int(track.get("mixed_samples", 0))
            if mixed_samples <= 0:
                continue
            start = int(track.get("start_sample", -1))
            end = int(track.get("end_sample", -1))
            if start < 0 or end < start or end > target_samples:
                raise ValueError(
                    f"dialogue_lock_manifest track {index} has an invalid sample interval "
                    f"[{start},{end}) for {target_samples} samples."
                )
            if end - start != mixed_samples:
                raise ValueError(
                    f"dialogue_lock_manifest track {index} interval length does not match mixed_samples."
                )
            final[..., start:end] = reference[..., start:end]

        return io.NodeOutput({"waveform": final, "sample_rate": reference_rate})
