"""Acoustic boundary snapping module for Cut Engine v2.

Eliminates audio clicks, pops, and phoneme artifacts by:
1. Searching ±snap_window_ms for the local minimum-energy sample.
2. Snapping the cut point to the nearest waveform zero-crossing.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

import numpy as np

if TYPE_CHECKING:
    from src.cut_engine.decision import KeepInterval

logger = logging.getLogger(__name__)


def find_lowest_energy_point(
    audio_data: np.ndarray,
    sample_rate: int,
    center_time_sec: float,
    window_ms: float = 25.0,
) -> int:
    """Find the sample index with the lowest local energy in ±window_ms around center_time_sec."""
    center_idx = int(center_time_sec * sample_rate)
    radius_samples = int((window_ms / 1000.0) * sample_rate)

    start_idx = max(0, center_idx - radius_samples)
    end_idx = min(len(audio_data), center_idx + radius_samples)

    if start_idx >= end_idx:
        return max(0, min(len(audio_data) - 1, center_idx))

    # Compute short sliding RMS (2ms window = 32 samples at 16kHz)
    kernel_size = max(4, int(sample_rate * 0.002))
    chunk = audio_data[start_idx:end_idx]

    # Quick squared energy convolution
    squared = chunk**2
    if len(squared) < kernel_size:
        min_local_idx = int(np.argmin(np.abs(chunk)))
        return start_idx + min_local_idx

    kernel = np.ones(kernel_size, dtype=np.float32) / kernel_size
    smoothed_energy = np.convolve(squared, kernel, mode="same")

    min_local_idx = int(np.argmin(smoothed_energy))
    return start_idx + min_local_idx


def snap_to_zero_crossing(
    audio_data: np.ndarray,
    sample_rate: int,
    sample_idx: int,
    search_radius_samples: int = 80,  # ~5ms at 16kHz
) -> int:
    """Find the closest zero-crossing (sign change or near-zero) around sample_idx."""
    n = len(audio_data)
    if n < 2:
        return max(0, sample_idx)

    sample_idx = max(0, min(n - 2, sample_idx))
    start = max(0, sample_idx - search_radius_samples)
    end = min(n - 1, sample_idx + search_radius_samples)

    sub = audio_data[start:end]
    # Check for sign change: s[i] * s[i+1] <= 0
    sign_changes = np.where((sub[:-1] * sub[1:]) <= 0)[0]

    if len(sign_changes) > 0:
        # Pick the sign change closest to target sample_idx
        orig_rel = sample_idx - start
        closest_sc = min(sign_changes, key=lambda idx: abs(idx - orig_rel))
        return start + closest_sc

    # Fallback: absolute minimum amplitude
    min_amp_rel = int(np.argmin(np.abs(sub)))
    return start + min_amp_rel


def snap_cut_intervals(
    audio_data: np.ndarray,
    sample_rate: int,
    intervals: list[KeepInterval],
    snap_window_ms: float = 25.0,
    clip_duration: float = 0.0,
) -> list[KeepInterval]:
    """Snap start and end of all keep intervals to zero-crossings at lowest energy."""
    if len(audio_data) < 100 or not intervals:
        return intervals

    snapped_count = 0
    for item in intervals:
        orig_s = item.start
        orig_e = item.end

        # 1. Snap start
        min_energy_s = find_lowest_energy_point(
            audio_data, sample_rate, orig_s, window_ms=snap_window_ms
        )
        zero_s = snap_to_zero_crossing(audio_data, sample_rate, min_energy_s)
        snapped_s = round(zero_s / float(sample_rate), 4)

        # 2. Snap end
        min_energy_e = find_lowest_energy_point(
            audio_data, sample_rate, orig_e, window_ms=snap_window_ms
        )
        zero_e = snap_to_zero_crossing(audio_data, sample_rate, min_energy_e)
        snapped_e = round(zero_e / float(sample_rate), 4)

        # Sanity check: keep reasonable minimum duration
        if snapped_e > snapped_s + 0.05:
            item.start = max(0.0, snapped_s)
            item.end = min(clip_duration, snapped_e) if clip_duration > 0 else snapped_e
            item.duration = round(item.end - item.start, 4)
            snapped_count += 1

    logger.debug(f"[snapping] Zero-crossing snapped {snapped_count} cut intervals (window=±{snap_window_ms}ms).")
    return intervals
