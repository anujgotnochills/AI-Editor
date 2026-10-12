"""FFmpeg rendering and transcript remapping for Cut Engine v2."""
from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from src.utils import ensure_ffmpeg_on_path

logger = logging.getLogger(__name__)
ensure_ffmpeg_on_path()


def remap_transcript_to_cuts(
    transcript: dict[str, Any],
    keep_segments: list[dict[str, float]],
) -> dict[str, Any]:
    """Map transcript word timestamps onto the new rough-cut timeline.

    Words falling inside removed sections are dropped. Words inside kept
    sections have their ``start`` and ``end`` adjusted so they are in sync
    with the shortened video.
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
        w_start = float(w["start"])
        w_end = float(w["end"])

        # Find the matching keep segment
        matched_seg_idx = -1
        best_overlap = 0.0

        for idx, seg in enumerate(keep_segments):
            overlap_start = max(w_start, seg["start"])
            overlap_end = min(w_end, seg["end"])
            overlap = overlap_end - overlap_start
            if overlap > best_overlap:
                best_overlap = overlap
                matched_seg_idx = idx

        if matched_seg_idx < 0 or best_overlap < 0.02:
            continue

        seg = keep_segments[matched_seg_idx]
        seg_new_start = new_timeline_starts[matched_seg_idx]

        clamped_start = max(w_start, seg["start"])
        clamped_end = min(w_end, seg["end"])

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
    """Render the rough cut video by concatenating the kept segments via FFmpeg."""
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
