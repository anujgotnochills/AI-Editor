"""
Cutter module: Silence and filler word removal (Rough Cut).

Features:
- Silence detection between speech intervals (configurable threshold, default 0.25s)
- Filler word detection ("um", "uh", "matlab", "basically", etc.)
- Acoustic safety padding (default 50ms) to prevent abrupt speech cutoffs
- Interval merging to preserve natural pacing and avoid micro-cuts
- Transcript remapping: recalculates word timestamps in the cut timeline
- Stream-accurate FFmpeg rough cut rendering with NVENC and libx264 fallback
"""
from __future__ import annotations

import json
import logging
import re
import string
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterable

from src.utils import ensure_ffmpeg_on_path
from src.cut_engine import CutEngine, CutEngineConfig, CutEnginePreset, CutEngineResult, get_preset_config

logger = logging.getLogger(__name__)

# Ensure ffmpeg is discovered on PATH
ensure_ffmpeg_on_path()

# Characters to strip when checking filler words
_PUNCT_TABLE = str.maketrans("", "", string.punctuation + "।,॥?!'\"-–—()[]{}")


def clean_word_text(text: str) -> str:
    """Normalize a word for comparison: lowercase, stripped of punctuation."""
    return text.translate(_PUNCT_TABLE).strip().lower()


# ─────────────────────────── Silence & Filler Detection ─────────────────────

def detect_silences(
    words: list[dict[str, Any]],
    clip_duration: float,
    min_duration: float = 0.25,
) -> list[dict[str, float]]:
    """Detect silence intervals longer than *min_duration* seconds.

    Checks:
    1. Leading silence: [0.0, first_word.start]
    2. Between consecutive words: [words[i].end, words[i+1].start]
    3. Trailing silence: [last_word.end, clip_duration]

    Returns:
        List of dicts: ``[{"start": float, "end": float, "duration": float}]``
    """
    if clip_duration <= 0:
        return []

    if not words:
        return [{"start": 0.0, "end": round(clip_duration, 3), "duration": round(clip_duration, 3)}]

    silences: list[dict[str, float]] = []

    # 1. Leading silence
    first_start = max(0.0, words[0]["start"])
    if first_start >= min_duration:
        silences.append({
            "start": 0.0,
            "end": round(first_start, 3),
            "duration": round(first_start, 3),
        })

    # 2. Inter-word silences
    for i in range(len(words) - 1):
        w_end = words[i]["end"]
        next_start = words[i + 1]["start"]
        gap = next_start - w_end
        if gap >= min_duration:
            silences.append({
                "start": round(w_end, 3),
                "end": round(next_start, 3),
                "duration": round(gap, 3),
            })

    # 3. Trailing silence
    last_end = words[-1]["end"]
    if clip_duration - last_end >= min_duration:
        silences.append({
            "start": round(last_end, 3),
            "end": round(clip_duration, 3),
            "duration": round(clip_duration - last_end, 3),
        })

    return silences


def detect_fillers(
    words: list[dict[str, Any]],
    fillers: Iterable[str],
) -> list[dict[str, Any]]:
    """Identify filler words based on a filler vocabulary list.

    Returns:
        List of flagged word entries with an added ``"index"`` key.
    """
    filler_set = {clean_word_text(f) for f in fillers if f}
    results: list[dict[str, Any]] = []

    for idx, w in enumerate(words):
        cleaned = clean_word_text(w.get("word", ""))
        if cleaned and cleaned in filler_set:
            entry = dict(w)
            entry["index"] = idx
            results.append(entry)

    return results


# ─────────────────────────── Keep Segments Calculation ──────────────────────

def build_keep_segments(
    words: list[dict[str, Any]],
    clip_duration: float,
    *,
    min_silence_sec: float = 0.25,
    padding_ms: float = 50.0,
    fillers: Iterable[str] | None = None,
) -> list[dict[str, float]]:
    """Compute the timeline intervals to KEEP from the clip.

    Rules:
    - Silence gaps >= *min_silence_sec* are removed.
    - Words matching *fillers* are removed.
    - A safety padding of *padding_ms* (default 50ms) is maintained around
      kept words so phonemes are never clipped.
    - Natural small pauses (< *min_silence_sec*) between kept words are preserved.
    - Adjacent or overlapping keep intervals are merged.

    Args:
        words:           List of word timestamp dicts.
        clip_duration:   Total clip duration in seconds.
        min_silence_sec: Minimum pause duration in seconds to trigger a cut.
        padding_ms:      Safety cushion in milliseconds added before and after words.
        fillers:         Optional list/set of filler words to cut.

    Returns:
        List of keep segments: ``[{"start": float, "end": float, "duration": float}]``.
    """
    if clip_duration <= 0:
        return []

    # If no words, keep entire clip as a safety fallback
    if not words:
        return [{"start": 0.0, "end": round(clip_duration, 3), "duration": round(clip_duration, 3)}]

    padding_sec = max(0.0, padding_ms / 1000.0)
    filler_set = {clean_word_text(f) for f in (fillers or []) if f}

    # Filter out filler words
    kept_words = [
        w for w in words
        if clean_word_text(w.get("word", "")) not in filler_set
    ]

    # If all words were fillers, keep entire clip as fallback with warning
    if not kept_words:
        logger.warning("[cutter] All words matched filler list; keeping entire clip.")
        return [{"start": 0.0, "end": round(clip_duration, 3), "duration": round(clip_duration, 3)}]

    # Compute raw intervals for each kept word with padding
    raw_intervals: list[tuple[float, float]] = []
    for w in kept_words:
        s = max(0.0, w["start"] - padding_sec)
        e = min(clip_duration, w["end"] + padding_sec)
        raw_intervals.append((s, e))

    # Also keep small natural pauses (< min_silence_sec) between consecutive kept words
    # Check original speech gaps between consecutive kept words:
    bridge_intervals: list[tuple[float, float]] = []
    for i in range(len(kept_words) - 1):
        prev_end = kept_words[i]["end"]
        next_start = kept_words[i + 1]["start"]
        gap = next_start - prev_end
        # If gap is smaller than min_silence_sec, bridge it completely so natural cadence remains
        if 0.0 <= gap < min_silence_sec:
            bridge_intervals.append((prev_end, next_start))

    all_intervals = sorted(raw_intervals + bridge_intervals, key=lambda x: x[0])

    # Merge overlapping or contiguous intervals
    merged: list[tuple[float, float]] = []
    for s, e in all_intervals:
        if not merged:
            merged.append((s, e))
        else:
            prev_s, prev_e = merged[-1]
            if s <= prev_e:  # overlap or touch
                merged[-1] = (prev_s, max(prev_e, e))
            else:
                merged.append((s, e))

    # Build output segments, filtering out zero-duration blips
    result: list[dict[str, float]] = []
    for s, e in merged:
        s_clamped = max(0.0, round(s, 3))
        e_clamped = min(round(clip_duration, 3), round(e, 3))
        dur = round(e_clamped - s_clamped, 3)
        if dur > 0.02:  # at least 20ms
            result.append({
                "start": s_clamped,
                "end": e_clamped,
                "duration": dur,
            })

    if not result:
        return [{"start": 0.0, "end": round(clip_duration, 3), "duration": round(clip_duration, 3)}]

    return result


# ─────────────────────────── Transcript Remapping ────────────────────────────

def remap_transcript_to_cuts(
    transcript: dict[str, Any],
    keep_segments: list[dict[str, float]],
) -> dict[str, Any]:
    """Map transcript word timestamps onto the new rough-cut timeline.

    Words falling inside removed sections are dropped. Words inside kept
    sections have their ``start`` and ``end`` adjusted so they are in sync
    with the shortened video.

    Args:
        transcript:    Original transcript dictionary.
        keep_segments: Ordered list of keep intervals from ``build_keep_segments()``.

    Returns:
        Updated transcript dict with remapped ``words``, updated ``duration_sec``,
        and re-grouped ``segments``.
    """
    orig_words = transcript.get("words", [])
    remapped_words: list[dict[str, Any]] = []

    # Precalculate start timestamp for each keep segment in the new timeline
    new_timeline_starts: list[float] = []
    cum_time = 0.0
    for seg in keep_segments:
        new_timeline_starts.append(cum_time)
        cum_time += seg["duration"]
    total_cut_duration = round(cum_time, 3)

    for w in orig_words:
        w_start = w["start"]
        w_end = w["end"]

        # Find the matching keep segment
        # A word belongs to a segment if it has significant overlap with it
        matched_seg_idx = -1
        best_overlap = 0.0

        for idx, seg in enumerate(keep_segments):
            overlap_start = max(w_start, seg["start"])
            overlap_end = min(w_end, seg["end"])
            overlap = overlap_end - overlap_start
            if overlap > best_overlap:
                best_overlap = overlap
                matched_seg_idx = idx

        # If the word does not overlap sufficiently with any kept segment, it was cut
        if matched_seg_idx < 0 or best_overlap < 0.02:
            continue

        seg = keep_segments[matched_seg_idx]
        seg_new_start = new_timeline_starts[matched_seg_idx]

        # Clamp word to segment boundaries
        clamped_start = max(w_start, seg["start"])
        clamped_end = min(w_end, seg["end"])

        # Offset within this keep segment
        offset_start = clamped_start - seg["start"]
        offset_end = clamped_end - seg["start"]

        new_w = dict(w)
        new_w["start"] = round(seg_new_start + offset_start, 3)
        new_w["end"] = round(seg_new_start + offset_end, 3)
        remapped_words.append(new_w)

    # Rebuild segments to match remapped words (group consecutive words)
    rebuilt_segments: list[dict[str, Any]] = []
    if remapped_words:
        current_chunk: list[dict[str, Any]] = []
        for w in remapped_words:
            if not current_chunk:
                current_chunk.append(w)
            else:
                gap = w["start"] - current_chunk[-1]["end"]
                # Split segments if gap > 0.4s or chunk gets longer than 12 words
                if gap > 0.4 or len(current_chunk) >= 12:
                    seg_text = " ".join(item["word"].strip() for item in current_chunk)
                    rebuilt_segments.append({
                        "start": current_chunk[0]["start"],
                        "end": current_chunk[-1]["end"],
                        "text": seg_text,
                        "words": list(current_chunk),
                    })
                    current_chunk = [w]
                else:
                    current_chunk.append(w)

        if current_chunk:
            seg_text = " ".join(item["word"].strip() for item in current_chunk)
            rebuilt_segments.append({
                "start": current_chunk[0]["start"],
                "end": current_chunk[-1]["end"],
                "text": seg_text,
                "words": list(current_chunk),
            })

    remapped = dict(transcript)
    remapped["duration_sec"] = total_cut_duration
    remapped["words"] = remapped_words
    remapped["segments"] = rebuilt_segments
    return remapped


# ─────────────────────────── FFmpeg Rendering ────────────────────────────────

def render_rough_cut(
    video_path: Path,
    keep_segments: list[dict[str, float]],
    output_path: Path,
    *,
    encoder: str = "h264_nvenc",
    fallback_encoder: str = "libx264",
    nvenc_preset: str = "p4",
    nvenc_cq: int = 23,
    libx264_crf: int = 23,
    libx264_preset: str = "fast",
    has_audio: bool = True,
) -> Path:
    """Render the rough cut video by concatenating the kept segments.

    Uses an FFmpeg filtergraph (``trim`` / ``atrim`` + ``concat``) passed
    via a temporary file to avoid command-line length limitations.
    Primary encoding uses *encoder* (NVENC), with automatic fallback to
    *fallback_encoder* (libx264) if hardware encoding is unavailable.

    Args:
        video_path:       Input source video file.
        keep_segments:    List of keep segments to retain.
        output_path:      Target output MP4 path.
        encoder:          Hardware encoder (default ``h264_nvenc``).
        fallback_encoder: Software encoder (default ``libx264``).
        has_audio:        Whether the input video contains an audio stream.

    Returns:
        The ``output_path`` of the rendered rough cut.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not keep_segments:
        raise ValueError("keep_segments cannot be empty for render_rough_cut.")

    # Build filtergraph script
    filter_lines: list[str] = []
    n = len(keep_segments)

    for idx, seg in enumerate(keep_segments):
        s = seg["start"]
        e = seg["end"]
        filter_lines.append(f"[0:v]trim=start={s}:end={e},setpts=PTS-STARTPTS[v{idx}];")
        if has_audio:
            filter_lines.append(f"[0:a]atrim=start={s}:end={e},asetpts=PTS-STARTPTS[a{idx}];")

    if has_audio:
        concat_inputs = "".join(f"[v{i}][a{i}]" for i in range(n))
        filter_lines.append(f"{concat_inputs}concat=n={n}:v=1:a=1[outv][outa]")
    else:
        concat_inputs = "".join(f"[v{i}]" for i in range(n))
        filter_lines.append(f"{concat_inputs}concat=n={n}:v=1:a=0[outv]")

    filter_script = "\n".join(filter_lines)

    # Write filtergraph to temporary file
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as tf:
        tf.write(filter_script)
        script_file = Path(tf.name)

    def _execute(enc: str) -> bool:
        if enc == "h264_nvenc":
            enc_args = ["-c:v", enc, "-preset", nvenc_preset, "-cq", str(nvenc_cq)]
        else:
            enc_args = ["-c:v", enc, "-crf", str(libx264_crf), "-preset", libx264_preset]

        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-filter_complex_script", str(script_file),
            "-map", "[outv]",
        ]
        if has_audio:
            cmd += ["-map", "[outa]", "-c:a", "aac", "-b:a", "192k"]

        cmd += enc_args + [str(output_path)]

        logger.info(f"[cutter] Rendering rough cut ({enc}, {n} segments)…")
        logger.debug("FFmpeg cmd: " + " ".join(cmd))
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            logger.warning(f"[cutter] FFmpeg failed with {enc}:\n{res.stderr[-1000:]}")
            return False
        return True

    try:
        success = _execute(encoder)
        if not success and fallback_encoder and fallback_encoder != encoder:
            logger.info(f"[cutter] Retrying rough cut render with {fallback_encoder}…")
            success = _execute(fallback_encoder)

        if not success:
            raise RuntimeError(f"FFmpeg rough cut rendering failed for {video_path.name}")
    finally:
        script_file.unlink(missing_ok=True)

    logger.info(
        f"[cutter] Rough cut ready: {output_path.name} "
        f"({output_path.stat().st_size / (1024 * 1024):.1f} MB)"
    )
    return output_path
