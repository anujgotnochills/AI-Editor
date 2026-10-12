"""Voice Activity Detection (VAD) module for Cut Engine v2.

Combines Silero neural VAD with an adaptive energy threshold (dynamic noise floor + margin)
to accurately detect speech boundaries without gating breaths or chopping quiet consonants.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import numpy as np
import soundfile as sf
import torch

logger = logging.getLogger(__name__)

# Cached Silero VAD model instance
_SILERO_MODEL: Any = None
_SILERO_UTILS: Any = None


def load_silero_vad() -> tuple[Any, Any]:
    """Load and cache the Silero VAD model."""
    global _SILERO_MODEL, _SILERO_UTILS
    if _SILERO_MODEL is not None and _SILERO_UTILS is not None:
        return _SILERO_MODEL, _SILERO_UTILS

    try:
        model, utils = torch.hub.load(
            repo_or_dir="snakers4/silero-vad",
            model="silero_vad",
            trust_repo=True,
            verbose=False,
        )
        model.eval()
        _SILERO_MODEL = model
        _SILERO_UTILS = utils
        logger.info("[vad] Silero VAD initialized successfully.")
        return _SILERO_MODEL, _SILERO_UTILS
    except Exception as exc:
        logger.warning(f"[vad] Silero VAD load from torch.hub failed ({exc}). Using energy-only fallback.")
        return None, None


def compute_adaptive_energy_profile(
    audio_data: np.ndarray,
    sample_rate: int = 16000,
    frame_ms: float = 20.0,
    hop_ms: float = 10.0,
    margin_db: float = 10.0,
) -> tuple[np.ndarray, float, float]:
    """Compute frame-level RMS energy and calculate an adaptive noise floor threshold.

    Returns:
        (energy_db_frames, noise_floor_db, threshold_db)
    """
    frame_size = int(sample_rate * (frame_ms / 1000.0))
    hop_size = int(sample_rate * (hop_ms / 1000.0))

    if len(audio_data) < frame_size:
        return np.array([-100.0]), -100.0, -90.0

    num_frames = max(1, 1 + (len(audio_data) - frame_size) // hop_size)
    rms_values = np.zeros(num_frames, dtype=np.float32)

    for i in range(num_frames):
        start = i * hop_size
        chunk = audio_data[start : start + frame_size]
        rms_values[i] = np.sqrt(np.mean(chunk**2) + 1e-12)

    energy_db = 20.0 * np.log10(rms_values + 1e-9)

    # Noise floor: 15th percentile of frame energy across the recording
    noise_floor_db = float(np.percentile(energy_db, 15))
    # Threshold: noise floor + user margin (clamped between -55 dB and -20 dB)
    threshold_db = float(np.clip(noise_floor_db + margin_db, -55.0, -20.0))

    return energy_db, noise_floor_db, threshold_db


def detect_speech_regions(
    wav_path: Path,
    *,
    vad_threshold: float = 0.50,
    noise_floor_margin_db: float = 10.0,
    min_speech_duration_ms: float = 100.0,
    min_silence_duration_ms: float = 150.0,
) -> list[dict[str, float]]:
    """Detect speech intervals using Silero VAD + adaptive energy threshold.

    Returns:
        List of speech intervals: ``[{"start": float, "end": float, "duration": float}]``.
    """
    wav_path = Path(wav_path)
    data, sr = sf.read(str(wav_path), dtype="float32")
    if data.ndim > 1:
        data = np.mean(data, axis=1)

    total_duration = len(data) / float(sr)
    if total_duration <= 0.05:
        return []

    # 1. Compute adaptive energy profile
    energy_db, noise_floor, threshold_db = compute_adaptive_energy_profile(
        data, sample_rate=sr, margin_db=noise_floor_margin_db
    )
    logger.debug(f"[vad] Noise floor: {noise_floor:.1f} dB, Energy threshold: {threshold_db:.1f} dB")

    # 2. Silero VAD pass
    silero_model, silero_utils = load_silero_vad()
    silero_intervals: list[dict[str, float]] = []

    if silero_model is not None and silero_utils is not None:
        try:
            get_speech_timestamps = silero_utils[0]
            wav_tensor = torch.from_numpy(data)
            timestamps = get_speech_timestamps(
                wav_tensor,
                silero_model,
                sampling_rate=sr,
                threshold=vad_threshold,
                min_speech_duration_ms=int(min_speech_duration_ms),
                min_silence_duration_ms=int(min_silence_duration_ms),
                return_seconds=True,
            )
            for ts in timestamps:
                s = round(float(ts["start"]), 3)
                e = round(float(ts["end"]), 3)
                silero_intervals.append({
                    "start": s,
                    "end": e,
                    "duration": round(e - s, 3),
                })
        except Exception as e_silero:
            logger.warning(f"[vad] Silero timestamp extraction threw: {e_silero}. Using energy.")
            silero_intervals = []

    # 3. Energy fallback if Silero produced nothing
    if not silero_intervals:
        hop_sec = 0.010
        is_speech = energy_db >= threshold_db
        in_speech = False
        start_t = 0.0

        for idx, active in enumerate(is_speech):
            t = idx * hop_sec
            if active and not in_speech:
                in_speech = True
                start_t = t
            elif not active and in_speech:
                in_speech = False
                dur = t - start_t
                if dur >= (min_speech_duration_ms / 1000.0):
                    silero_intervals.append({
                        "start": round(start_t, 3),
                        "end": round(t, 3),
                        "duration": round(dur, 3),
                    })
        if in_speech:
            silero_intervals.append({
                "start": round(start_t, 3),
                "end": round(total_duration, 3),
                "duration": round(total_duration - start_t, 3),
            })

    # 4. Energy validation: ensure Silero intervals are not pure noise
    validated: list[dict[str, float]] = []
    hop_sec = 0.010

    for interval in silero_intervals:
        s_idx = max(0, int(interval["start"] / hop_sec))
        e_idx = min(len(energy_db), int(interval["end"] / hop_sec) + 1)
        if s_idx < e_idx:
            peak_db = float(np.max(energy_db[s_idx:e_idx]))
            # If the peak energy is above noise floor + 3 dB, keep it
            if peak_db > (noise_floor + 3.0):
                validated.append(interval)
        else:
            validated.append(interval)

    # 5. Merge very close speech intervals (< 120ms gap) to keep natural flow
    merged: list[dict[str, float]] = []
    for item in validated:
        if not merged:
            merged.append(item)
        else:
            prev = merged[-1]
            gap = item["start"] - prev["end"]
            if gap < 0.12:  # bridge gaps under 120ms
                prev["end"] = item["end"]
                prev["duration"] = round(item["end"] - prev["start"], 3)
            else:
                merged.append(item)

    logger.info(f"[vad] Detected {len(merged)} speech regions (total speech: {sum(x['duration'] for x in merged):.1f}s / {total_duration:.1f}s)")
    return merged
