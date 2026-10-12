"""
Ingest module: scan footage folder, probe every clip with ffprobe,
flag quality issues, and cache results per project.

Cache key: MD5(resolved_path + file_size + mtime_ns)
→ unchanged clips are never re-probed across runs.
"""
from __future__ import annotations

import json
import hashlib
import logging
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Recognised video file extensions (lowercase checked at runtime)
VIDEO_EXTENSIONS: frozenset[str] = frozenset(
    {".mp4", ".mov", ".avi", ".mkv", ".mts", ".m2ts", ".webm", ".m4v"}
)


# ─────────────────────────── helpers ────────────────────────────────────────

def _file_key(path: Path) -> str:
    """Fast cache key using path + file metadata (no content read)."""
    st = path.stat()
    raw = f"{path.resolve()}:{st.st_size}:{st.st_mtime_ns}"
    return hashlib.md5(raw.encode()).hexdigest()


def _ffprobe(path: Path) -> dict[str, Any]:
    """Run ffprobe on *path* and return the parsed JSON output."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format", "-show_streams",
        str(path),
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=True, timeout=60
        )
    except FileNotFoundError:
        raise RuntimeError(
            "ffprobe not found.  Install FFmpeg and ensure it is on your PATH.\n"
            "  Windows: https://www.gyan.dev/ffmpeg/builds/"
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"ffprobe failed for {path.name}: {exc.stderr.strip()}"
        ) from exc
    return json.loads(result.stdout)


def _parse_fps(fps_str: str) -> float:
    """Parse '30000/1001' or '30' fps strings → float."""
    if "/" in fps_str:
        num, den = fps_str.split("/", 1)
        return round(int(num) / max(int(den), 1), 3)
    return round(float(fps_str), 3)


def _extract(path: Path, probe: dict[str, Any]) -> dict[str, Any]:
    """Flatten the most useful ffprobe fields into a clean dict."""
    fmt = probe.get("format", {})
    streams = probe.get("streams", [])

    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    width = int(video.get("width", 0)) if video else 0
    height = int(video.get("height", 0)) if video else 0
    fps = _parse_fps(video.get("r_frame_rate", "0/1")) if video else 0.0

    # Duration: prefer format-level, fall back to video stream
    duration = float(
        fmt.get("duration")
        or (video or {}).get("duration")
        or 0
    )

    if height > width:
        orientation = "vertical"
    elif width > height:
        orientation = "horizontal"
    else:
        orientation = "square"

    flags: list[str] = []
    if not audio:
        flags.append("NO_AUDIO")
    if duration < 2.0:
        flags.append("TOO_SHORT")
    if 0 < fps < 23:
        flags.append("LOW_FPS")
    if 0 < width < 720:
        flags.append("LOW_RESOLUTION")

    return {
        "path": str(path.resolve()),
        "filename": path.name,
        "stem": path.stem,
        "duration_sec": round(duration, 3),
        "width": width,
        "height": height,
        "fps": fps,
        "orientation": orientation,
        "has_audio": audio is not None,
        "audio_codec": audio.get("codec_name") if audio else None,
        "video_codec": video.get("codec_name") if video else None,
        "size_mb": round(int(fmt.get("size", 0)) / 1_048_576, 2),
        "flags": flags,
    }


# ─────────────────────────── public API ─────────────────────────────────────

def ingest(footage_dir: Path, cache_dir: Path) -> list[dict[str, Any]]:
    """Scan *footage_dir* for video clips and return their metadata.

    Results are cached in ``cache_dir/ingest_cache.json``.
    Clips whose path/size/mtime haven't changed are returned from cache
    without re-running ffprobe.

    Args:
        footage_dir: Directory containing raw footage files.
        cache_dir:   Per-project cache directory.

    Returns:
        List of clip metadata dicts, sorted by filename.
        Each dict contains: path, filename, stem, duration_sec, width,
        height, fps, orientation, has_audio, audio_codec, video_codec,
        size_mb, flags.
    """
    footage_dir = Path(footage_dir)
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    cache_file = cache_dir / "ingest_cache.json"
    cache: dict[str, dict] = {}
    if cache_file.exists():
        try:
            cache = json.loads(cache_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("[ingest] Corrupt cache — rebuilding.")

    clips: list[dict] = []
    dirty = False

    for path in sorted(footage_dir.iterdir()):
        if path.suffix.lower() not in VIDEO_EXTENSIONS or not path.is_file():
            continue

        key = _file_key(path)
        if key in cache:
            logger.debug(f"[ingest] Cache hit: {path.name}")
            clips.append(cache[key])
            continue

        logger.info(f"[ingest] Probing:    {path.name}")
        try:
            probe = _ffprobe(path)
            meta = _extract(path, probe)
            cache[key] = meta
            clips.append(meta)
            dirty = True
        except Exception as exc:
            logger.error(f"[ingest] Skipping {path.name}: {exc}")

    if dirty:
        cache_file.write_text(
            json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    logger.info(f"[ingest] Found {len(clips)} clip(s) in {footage_dir}")
    return clips
