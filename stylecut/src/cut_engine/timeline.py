"""Timeline frame grid and cut list generator for Cut Engine v2.

Converts floating-point cut times to exact integer frame numbers using rational FPS
fractions (never rounding in floating point seconds) to eliminate timeline drift.
Outputs cut_list.json with J/L cut support, crossfades, reasons, and confidence ratings.
"""
from __future__ import annotations

import json
import logging
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any, Optional

from src.cut_engine.config import PacingConfig
from src.cut_engine.decision import KeepInterval

logger = logging.getLogger(__name__)


def time_to_frame(seconds: float, fps: Fraction) -> int:
    """Convert exact seconds to an integer frame number using rational arithmetic."""
    # Convert float to Decimal/Fraction cleanly
    dec_sec = Decimal(str(round(seconds, 6)))
    frac_sec = Fraction(dec_sec)
    exact_frame = frac_sec * fps
    return int(round(float(exact_frame)))


def frame_to_time(frame: int, fps: Fraction) -> float:
    """Convert integer frame number to exact seconds."""
    return float(Fraction(frame) / fps)


def build_cut_list(
    intervals: list[KeepInterval],
    source_clip_path: Path,
    fps: Fraction,
    clip_duration_sec: float,
    pacing: PacingConfig,
    width: int = 1080,
    height: int = 1920,
    sample_rate: int = 48000,
) -> dict[str, Any]:
    """Build the frame-accurate cut list dictionary with exact rational frame numbers."""
    source_clip_path = Path(source_clip_path)
    total_src_frames = time_to_frame(clip_duration_sec, fps)

    crossfade_frames = max(1, time_to_frame(pacing.crossfade_ms / 1000.0, fps))
    j_cut_frames = time_to_frame(abs(pacing.j_cut_offset_ms) / 1000.0, fps)
    l_cut_frames = time_to_frame(abs(pacing.l_cut_offset_ms) / 1000.0, fps)

    cuts: list[dict[str, Any]] = []
    current_timeline_frame = 0

    for idx, item in enumerate(intervals):
        src_in_f = time_to_frame(item.start, fps)
        src_out_f = time_to_frame(item.end, fps)

        # Clamping
        src_in_f = max(0, min(total_src_frames, src_in_f))
        src_out_f = max(src_in_f + 1, min(total_src_frames, src_out_f))
        dur_f = src_out_f - src_in_f

        # Audio in/out with optional J-cut and L-cut offsets
        audio_in_f = max(0, src_in_f - j_cut_frames)
        audio_out_f = min(total_src_frames, src_out_f + l_cut_frames)

        tl_in_f = current_timeline_frame
        tl_out_f = current_timeline_frame + dur_f
        current_timeline_frame = tl_out_f

        cuts.append({
            "segment_id": f"seg_{idx:03d}",
            "source_in_frame": src_in_f,
            "source_out_frame": src_out_f,
            "duration_frames": dur_f,
            "source_in_sec": round(frame_to_time(src_in_f, fps), 4),
            "source_out_sec": round(frame_to_time(src_out_f, fps), 4),
            "duration_sec": round(frame_to_time(dur_f, fps), 4),
            "timeline_in_frame": tl_in_f,
            "timeline_out_frame": tl_out_f,
            "timeline_in_sec": round(frame_to_time(tl_in_f, fps), 4),
            "timeline_out_sec": round(frame_to_time(tl_out_f, fps), 4),
            "audio_in_frame": audio_in_f,
            "audio_out_frame": audio_out_f,
            "audio_duration_frames": audio_out_f - audio_in_f,
            "crossfade_frames": crossfade_frames,
            "confidence": round(item.confidence, 2),
            "reason": item.reason,
            "flagged": item.flagged or (item.confidence < 0.70),
            "words": item.words,
        })

    total_cut_frames = current_timeline_frame
    total_cut_sec = frame_to_time(total_cut_frames, fps)
    saved_sec = max(0.0, clip_duration_sec - total_cut_sec)
    pct_saved = (saved_sec / clip_duration_sec * 100.0) if clip_duration_sec > 0 else 0.0

    cut_list_data = {
        "source_clip": {
            "path": str(source_clip_path.resolve()),
            "filename": source_clip_path.name,
            "width": width,
            "height": height,
            "sample_rate": sample_rate,
            "duration_sec": round(clip_duration_sec, 4),
            "total_frames": total_src_frames,
        },
        "timebase": {
            "fps_numerator": fps.numerator,
            "fps_denominator": fps.denominator,
            "fps": round(float(fps), 3),
        },
        "pacing_applied": {
            "min_silence_sec": pacing.min_silence_sec,
            "keep_gap_ms": pacing.keep_gap_ms,
            "padding_before_ms": pacing.padding_before_ms,
            "padding_after_ms": pacing.padding_after_ms,
            "snap_window_ms": pacing.snap_window_ms,
            "crossfade_ms": pacing.crossfade_ms,
        },
        "summary": {
            "cuts_count": len(cuts),
            "original_duration_sec": round(clip_duration_sec, 3),
            "cut_duration_sec": round(total_cut_sec, 3),
            "saved_duration_sec": round(saved_sec, 3),
            "saved_percent": round(pct_saved, 1),
            "original_frames": total_src_frames,
            "cut_frames": total_cut_frames,
        },
        "cuts": cuts,
    }

    return cut_list_data
