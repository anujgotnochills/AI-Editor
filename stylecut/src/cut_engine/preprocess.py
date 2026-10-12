"""Preprocessing module for Cut Engine v2.

Handles:
- Media probing (ffprobe)
- VFR (Variable Frame Rate) detection and conversion to CFR (Constant Frame Rate)
- 16 kHz mono 16-bit PCM audio extraction for precision VAD and waveform analysis
"""
from __future__ import annotations

import json
import logging
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any, Optional

from src.utils import ensure_ffmpeg_on_path

logger = logging.getLogger(__name__)
ensure_ffmpeg_on_path()


def probe_media(path: Path) -> dict[str, Any]:
    """Run ffprobe on path and return streams and format metadata."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format", "-show_streams",
        str(path),
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=60)
        return json.loads(res.stdout)
    except Exception as exc:
        logger.error(f"[preprocess] ffprobe failed for {path}: {exc}")
        raise RuntimeError(f"ffprobe failed for {path.name}: {exc}") from exc


def parse_fps(r_frame_rate: str, avg_frame_rate: str = "") -> tuple[float, Fraction]:
    """Parse frame rate string into float and exact Fraction."""
    rate_str = r_frame_rate or avg_frame_rate or "30/1"
    try:
        if "/" in rate_str:
            num, den = rate_str.split("/", 1)
            frac = Fraction(int(num), max(1, int(den)))
        else:
            frac = Fraction(float(rate_str)).limit_denominator(1001)
        return float(frac), frac
    except Exception:
        return 30.0, Fraction(30, 1)


def detect_vfr(media_path: Path) -> tuple[bool, str, float, Fraction]:
    """Detect whether media is Variable Frame Rate (VFR).

    Returns:
        (is_vfr, rate_string, float_fps, rational_fps)
    """
    probe = probe_media(media_path)
    streams = probe.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video:
        return False, "30/1", 30.0, Fraction(30, 1)

    r_rate = video.get("r_frame_rate", "30/1")
    avg_rate = video.get("avg_frame_rate", "30/1")

    fps_r, frac_r = parse_fps(r_rate)
    fps_avg, frac_avg = parse_fps(avg_rate)

    # If r_frame_rate differs significantly from avg_frame_rate, file is VFR
    is_vfr = abs(fps_r - fps_avg) > 0.08 and fps_r > 0 and fps_avg > 0
    chosen_fps = fps_r if fps_r > 0 else (fps_avg if fps_avg > 0 else 30.0)
    chosen_frac = frac_r if fps_r > 0 else frac_avg

    if is_vfr:
        logger.warning(
            f"[preprocess] VFR detected in {media_path.name}: r_fps={fps_r:.3f}, avg_fps={fps_avg:.3f}. "
            "Converting to CFR is recommended to prevent A/V desync."
        )

    return is_vfr, r_rate, chosen_fps, chosen_frac


def convert_to_cfr_if_needed(
    video_path: Path,
    output_path: Path,
    target_fps: Optional[float] = None,
    force: bool = False,
) -> tuple[Path, bool]:
    """Convert variable frame rate (VFR) video to constant frame rate (CFR) if detected or forced.

    Returns:
        (resulting_path, was_converted)
    """
    video_path = Path(video_path)
    is_vfr, _, fps, frac = detect_vfr(video_path)

    if not is_vfr and not force:
        return video_path, False

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fps_val = target_fps or float(frac)
    fps_str = f"{fps_val:.3f}" if (fps_val % 1 != 0) else f"{int(fps_val)}"

    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vf", f"fps={fps_str}",
        "-vsync", "cfr",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "18",
        "-c:a", "aac",
        "-b:a", "192k",
        str(output_path),
    ]

    logger.info(f"[preprocess] Converting VFR → CFR ({fps_str} fps): {video_path.name} -> {output_path.name}")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0 or not output_path.exists():
        logger.warning(f"[preprocess] CFR conversion failed, falling back to original: {res.stderr[:200]}")
        return video_path, False

    return output_path, True


def extract_analysis_audio(
    media_path: Path,
    output_wav_path: Path,
    sample_rate: int = 16000,
) -> Path:
    """Extract standard mono 16 kHz 16-bit PCM WAV for Silero VAD and snapping analysis.

    Uses caching: if output file exists and is newer than source, re-uses it.
    """
    media_path = Path(media_path)
    output_wav_path = Path(output_wav_path)
    output_wav_path.parent.mkdir(parents=True, exist_ok=True)

    if output_wav_path.exists() and output_wav_path.stat().st_size > 1000:
        if output_wav_path.stat().st_mtime >= media_path.stat().st_mtime:
            return output_wav_path

    cmd = [
        "ffmpeg", "-y",
        "-i", str(media_path),
        "-vn",
        "-ar", str(sample_rate),
        "-ac", "1",
        "-c:a", "pcm_s16le",
        str(output_wav_path),
    ]

    logger.info(f"[preprocess] Extracting analysis audio ({sample_rate} Hz mono): {output_wav_path.name}")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0 or not output_wav_path.exists() or output_wav_path.stat().st_size < 100:
        raise RuntimeError(f"Failed to extract analysis audio from {media_path}: {res.stderr[:300]}")

    return output_wav_path
