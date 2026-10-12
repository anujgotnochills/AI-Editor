"""Tests for src/transcribe.py — uses mocks so no GPU or model download needed."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.transcribe import transcribe_clip, transcribe_project, _clip_key


# ─────────────────────────── helpers ────────────────────────────────────────

def _mock_whisper(words=None):
    """Build a minimal faster-whisper mock matching the real API surface."""
    if words is None:
        words = [
            MagicMock(word=" Hello", start=0.0, end=0.5, probability=0.99),
            MagicMock(word=" world", start=0.5, end=1.0, probability=0.98),
        ]
    seg = MagicMock()
    seg.start = 0.0
    seg.end = 1.0
    seg.text = " Hello world"
    seg.words = words

    info = MagicMock()
    info.language = "en"
    info.language_probability = 0.99
    info.duration = 5.0

    model = MagicMock()
    model.transcribe.return_value = (iter([seg]), info)
    return model


# ─────────────────────────── unit tests ─────────────────────────────────────

def test_clip_key_is_md5(sample_video: Path):
    key = _clip_key(sample_video)
    assert len(key) == 32
    assert key == _clip_key(sample_video)  # stable across calls


# ─────────────────────────── cache tests ─────────────────────────────────────

def test_transcribe_returns_cache_without_loading_model(tmp_path: Path, sample_video: Path):
    """If a cache file already exists, _load_whisper must never be called."""
    cache_dir = tmp_path / "transcripts"
    cache_dir.mkdir()

    key = _clip_key(sample_video)
    fake = {
        "clip": str(sample_video),
        "language": "en",
        "language_probability": 0.99,
        "duration_sec": 5.0,
        "segments": [],
        "words": [],
    }
    (cache_dir / f"{key}.json").write_text(json.dumps(fake), encoding="utf-8")

    with patch("src.transcribe._load_whisper") as mock_load:
        result = transcribe_clip(sample_video, cache_dir)
        mock_load.assert_not_called()

    assert result["language"] == "en"


def test_transcribe_writes_cache_after_run(tmp_path: Path, sample_video: Path):
    cache_dir = tmp_path / "transcripts"
    mock_model = _mock_whisper()

    with patch("src.transcribe._load_whisper", return_value=mock_model):
        transcribe_clip(sample_video, cache_dir)

    key = _clip_key(sample_video)
    cache_file = cache_dir / f"{key}.json"
    assert cache_file.exists()
    data = json.loads(cache_file.read_text(encoding="utf-8"))
    assert data["language"] == "en"
    assert len(data["words"]) == 2


# ─────────────────────────── fallback tests ──────────────────────────────────

def test_gpu_failure_triggers_cpu_fallback(tmp_path: Path, sample_video: Path):
    """CUDA load failure must silently fall back to CPU model."""
    cpu_model = _mock_whisper()

    def _load_side_effect(model, device, compute_type):
        if device == "cuda":
            raise RuntimeError("CUDA OOM — simulated")
        return cpu_model

    with patch("src.transcribe._load_whisper", side_effect=_load_side_effect):
        result = transcribe_clip(
            sample_video,
            tmp_path / "transcripts",
            device="cuda",
        )

    assert result["language"] == "en"


# ─────────────────────────── full result shape ───────────────────────────────

def test_transcribe_result_shape(tmp_path: Path, sample_video: Path):
    mock_model = _mock_whisper()
    with patch("src.transcribe._load_whisper", return_value=mock_model):
        result = transcribe_clip(sample_video, tmp_path / "transcripts")

    # Top-level keys
    for key in ("clip", "language", "language_probability", "duration_sec", "segments", "words"):
        assert key in result, f"Missing key: {key}"

    # Word entries
    for w in result["words"]:
        assert "word" in w
        assert "start" in w
        assert "end" in w
        assert w["start"] <= w["end"]


# ─────────────────────────── project-level ───────────────────────────────────

def test_transcribe_project_skips_no_audio(tmp_path: Path, sample_video: Path):
    """Clips flagged NO_AUDIO must be silently skipped."""
    clips = [
        {
            "path": str(sample_video),
            "filename": sample_video.name,
            "has_audio": False,
            "flags": ["NO_AUDIO"],
        }
    ]
    mock_model = _mock_whisper()
    with patch("src.transcribe._load_whisper", return_value=mock_model):
        results = transcribe_project(clips, tmp_path / "transcripts", {})

    assert results == {}  # nothing transcribed


def test_transcribe_project_returns_dict_keyed_by_filename(tmp_path: Path, sample_video: Path):
    clips = [
        {
            "path": str(sample_video),
            "filename": sample_video.name,
            "has_audio": True,
            "flags": [],
        }
    ]
    mock_model = _mock_whisper()
    with patch("src.transcribe._load_whisper", return_value=mock_model):
        results = transcribe_project(clips, tmp_path / "transcripts", {})

    assert sample_video.name in results
    assert results[sample_video.name]["language"] == "en"
