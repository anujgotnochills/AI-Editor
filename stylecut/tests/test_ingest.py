"""Tests for src/ingest.py."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from src.ingest import ingest, _file_key, _parse_fps


# ─────────────────────────── unit tests ──────────────────────────────────────

def test_parse_fps_fraction():
    assert _parse_fps("30000/1001") == pytest.approx(29.97, abs=0.01)


def test_parse_fps_integer():
    assert _parse_fps("30") == 30.0


def test_parse_fps_zero_denominator():
    # Should not divide by zero
    result = _parse_fps("0/0")
    assert result == 0.0


def test_file_key_is_stable(sample_video: Path):
    k1 = _file_key(sample_video)
    k2 = _file_key(sample_video)
    assert k1 == k2
    assert len(k1) == 32  # MD5 hex digest length


# ─────────────────────────── integration tests ───────────────────────────────

def test_ingest_empty_folder(tmp_path: Path):
    footage = tmp_path / "footage"
    footage.mkdir()
    result = ingest(footage, tmp_path / "cache")
    assert result == []


def test_ingest_ignores_non_video_files(tmp_path: Path):
    footage = tmp_path / "footage"
    footage.mkdir()
    (footage / "readme.txt").write_text("ignore me")
    (footage / "image.jpg").write_bytes(b"\xff\xd8\xff")
    result = ingest(footage, tmp_path / "cache")
    assert result == []


def test_ingest_with_real_clip(tmp_path: Path, sample_video: Path):
    footage = tmp_path / "footage"
    footage.mkdir()
    shutil.copy(sample_video, footage / sample_video.name)

    result = ingest(footage, tmp_path / "cache")
    assert len(result) == 1
    clip = result[0]
    assert clip["filename"] == sample_video.name
    assert clip["duration_sec"] == pytest.approx(5.0, abs=0.5)
    assert clip["has_audio"] is True
    assert clip["width"] == 640
    assert clip["height"] == 480
    assert clip["fps"] == pytest.approx(30.0, abs=0.1)
    assert clip["orientation"] == "horizontal"
    assert clip["flags"] == ["LOW_RESOLUTION"]


def test_ingest_caches_results(tmp_path: Path, sample_video: Path):
    """Second call must return identical results without re-running ffprobe."""
    footage = tmp_path / "footage"
    footage.mkdir()
    shutil.copy(sample_video, footage / sample_video.name)
    cache = tmp_path / "cache"

    r1 = ingest(footage, cache)
    r2 = ingest(footage, cache)
    assert r1 == r2
    # Cache file must exist (ingest saves to cache_dir/ingest_cache.json)
    assert (cache / "ingest_cache.json").exists()


def test_ingest_detects_missing_audio(tmp_path: Path):
    """A clip with no audio track must be flagged NO_AUDIO."""
    footage = tmp_path / "footage"
    footage.mkdir()
    no_audio = footage / "silent.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "color=c=red:size=320x240:duration=3:rate=30",
        "-an",  # no audio
        "-c:v", "libx264", "-crf", "30", "-preset", "ultrafast",
        str(no_audio),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True)
    except FileNotFoundError:
        pytest.skip("ffmpeg not on PATH — skipping")
    if r.returncode != 0:
        pytest.skip("ffmpeg not available")

    result = ingest(footage, tmp_path / "cache")
    assert len(result) == 1
    assert "NO_AUDIO" in result[0]["flags"]
    assert result[0]["has_audio"] is False
