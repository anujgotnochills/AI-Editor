"""Tests for src/cutter.py — Silence and Filler Word Removal (Phase 2)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from src.cutter import (
    clean_word_text,
    detect_silences,
    detect_fillers,
    build_keep_segments,
    remap_transcript_to_cuts,
    render_rough_cut,
)


# ─────────────────────────── Unit: clean_word_text ─────────────────────────

def test_clean_word_text_punctuation_and_case():
    assert clean_word_text("  Hello,  ") == "hello"
    assert clean_word_text("UM...") == "um"
    assert clean_word_text("Matlab?!") == "matlab"
    assert clean_word_text("basically—") == "basically"
    assert clean_word_text("पानी,") == "पानी"


# ─────────────────────────── Unit: detect_silences ─────────────────────────

def test_detect_silences_basic():
    words = [
        {"word": "first", "start": 1.0, "end": 2.0},
        {"word": "second", "start": 3.0, "end": 4.0},
        {"word": "third", "start": 4.1, "end": 4.5},
    ]
    # Leading silence: 0.0 -> 1.0 (1.0s)
    # Gap 1: 2.0 -> 3.0 (1.0s)
    # Gap 2: 4.0 -> 4.1 (0.1s, < 0.25s so skipped)
    # Trailing silence: 4.5 -> 6.0 (1.5s)
    silences = detect_silences(words, clip_duration=6.0, min_duration=0.25)

    assert len(silences) == 3
    assert silences[0] == {"start": 0.0, "end": 1.0, "duration": 1.0}
    assert silences[1] == {"start": 2.0, "end": 3.0, "duration": 1.0}
    assert silences[2] == {"start": 4.5, "end": 6.0, "duration": 1.5}


def test_detect_silences_no_leading_or_trailing():
    words = [
        {"word": "start", "start": 0.05, "end": 1.0},
        {"word": "end", "start": 1.05, "end": 2.0},
    ]
    # Both gaps are 0.05s, clip duration 2.05s
    silences = detect_silences(words, clip_duration=2.05, min_duration=0.25)
    assert len(silences) == 0


def test_detect_silences_empty_words():
    silences = detect_silences([], clip_duration=5.0, min_duration=0.25)
    assert len(silences) == 1
    assert silences[0] == {"start": 0.0, "end": 5.0, "duration": 5.0}


# ─────────────────────────── Unit: detect_fillers ──────────────────────────

def test_detect_fillers():
    words = [
        {"word": "Hello", "start": 0.0, "end": 0.5},
        {"word": "um,", "start": 0.6, "end": 0.8},
        {"word": "this", "start": 0.9, "end": 1.1},
        {"word": "Matlab", "start": 1.2, "end": 1.5},
        {"word": "world", "start": 1.6, "end": 2.0},
    ]
    fillers = ["um", "uh", "matlab"]
    detected = detect_fillers(words, fillers)

    assert len(detected) == 2
    assert detected[0]["word"] == "um,"
    assert detected[0]["index"] == 1
    assert detected[1]["word"] == "Matlab"
    assert detected[1]["index"] == 3


# ─────────────────────────── Unit: build_keep_segments ─────────────────────

def test_build_keep_segments_removes_silence_and_preserves_padding():
    words = [
        {"word": "Hello", "start": 1.0, "end": 2.0},
        {"word": "world", "start": 3.0, "end": 4.0},
    ]
    # 50ms padding:
    # Word 1: [0.95, 2.05]
    # Word 2: [2.95, 4.05]
    # Pause 2.0 to 3.0 (1.0s) is cut.
    segs = build_keep_segments(
        words,
        clip_duration=6.0,
        min_silence_sec=0.25,
        padding_ms=50.0,
    )
    assert len(segs) == 2
    assert segs[0]["start"] == 0.95
    assert segs[0]["end"] == 2.05
    assert segs[1]["start"] == 2.95
    assert segs[1]["end"] == 4.05


def test_build_keep_segments_preserves_micro_pauses():
    words = [
        {"word": "Quick", "start": 0.5, "end": 1.0},
        {"word": "brown", "start": 1.15, "end": 1.5},  # 0.15s gap < 0.25s
    ]
    segs = build_keep_segments(
        words,
        clip_duration=3.0,
        min_silence_sec=0.25,
        padding_ms=50.0,
    )
    # The two words should be bridged into a single continuous segment
    assert len(segs) == 1
    assert segs[0]["start"] == 0.45  # 0.5 - 0.05
    assert segs[0]["end"] == 1.55    # 1.5 + 0.05


def test_build_keep_segments_removes_filler_words():
    words = [
        {"word": "Start", "start": 0.5, "end": 1.0},
        {"word": "um", "start": 1.5, "end": 1.9},     # filler to cut
        {"word": "finish", "start": 2.5, "end": 3.0},
    ]
    segs = build_keep_segments(
        words,
        clip_duration=4.0,
        min_silence_sec=0.25,
        padding_ms=50.0,
        fillers=["um", "uh"],
    )
    assert len(segs) == 2
    assert segs[0]["start"] == 0.45
    assert segs[0]["end"] == 1.05
    assert segs[1]["start"] == 2.45
    assert segs[1]["end"] == 3.05


def test_build_keep_segments_clamping_to_boundaries():
    words = [
        {"word": "Start", "start": 0.02, "end": 1.0},
        {"word": "End", "start": 1.5, "end": 2.98},
    ]
    segs = build_keep_segments(
        words,
        clip_duration=3.0,
        min_silence_sec=0.25,
        padding_ms=50.0,
    )
    assert segs[0]["start"] == 0.0  # clamped from 0.02 - 0.05 = -0.03
    assert segs[-1]["end"] == 3.0   # clamped from 2.98 + 0.05 = 3.03


# ─────────────────────────── Unit: remap_transcript_to_cuts ─────────────────

def test_remap_transcript_to_cuts():
    orig_transcript = {
        "clip": "test.mp4",
        "duration_sec": 10.0,
        "words": [
            {"word": "Hello", "start": 1.0, "end": 2.0, "word_hinglish": "Hello"},
            {"word": "um", "start": 2.2, "end": 2.5, "word_hinglish": "um"},
            {"word": "world", "start": 4.0, "end": 5.0, "word_hinglish": "duniya"},
        ],
        "segments": [],
    }

    # Cut segments:
    # Seg 0: [0.95, 2.05] (dur 1.10)
    # Seg 1: [3.95, 5.05] (dur 1.10)
    # "um" at 2.2-2.5 was excluded.
    keep_segments = [
        {"start": 0.95, "end": 2.05, "duration": 1.10},
        {"start": 3.95, "end": 5.05, "duration": 1.10},
    ]

    remapped = remap_transcript_to_cuts(orig_transcript, keep_segments)

    assert remapped["duration_sec"] == 2.20
    remapped_words = remapped["words"]
    assert len(remapped_words) == 2  # "um" is excluded!

    # Word 1: was 1.0-2.0 in [0.95, 2.05]. Offset: 0.05 to 1.05
    assert remapped_words[0]["word"] == "Hello"
    assert remapped_words[0]["start"] == 0.05
    assert remapped_words[0]["end"] == 1.05
    assert remapped_words[0]["word_hinglish"] == "Hello"

    # Word 2: was 4.0-5.0 in [3.95, 5.05]. Seg 1 starts at 1.10 in cut timeline.
    # Offset: 4.0 - 3.95 = 0.05 -> 1.10 + 0.05 = 1.15
    # Offset: 5.0 - 3.95 = 1.05 -> 1.10 + 1.05 = 2.15
    assert remapped_words[1]["word"] == "world"
    assert remapped_words[1]["start"] == 1.15
    assert remapped_words[1]["end"] == 2.15
    assert remapped_words[1]["word_hinglish"] == "duniya"

    # Segments should be rebuilt
    assert len(remapped["segments"]) > 0


# ─────────────────────────── Integration: render_rough_cut ─────────────────

def test_render_rough_cut_with_sample_video(sample_video: Path, tmp_path: Path):
    """Render a rough cut from the 5-second sample_video fixture."""
    keep_segments = [
        {"start": 0.5, "end": 1.5, "duration": 1.0},
        {"start": 2.5, "end": 3.5, "duration": 1.0},
    ]
    out_path = tmp_path / "rough_cut.mp4"

    res = render_rough_cut(
        video_path=sample_video,
        keep_segments=keep_segments,
        output_path=out_path,
        encoder="h264_nvenc",
        fallback_encoder="libx264",
    )

    assert res.exists()
    assert res.stat().st_size > 0

    # Probe duration with ffprobe
    cmd = [
        "ffprobe", "-v", "quiet",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(res),
    ]
    dur_proc = subprocess.run(cmd, capture_output=True, text=True)
    if dur_proc.returncode == 0:
        cut_duration = float(dur_proc.stdout.strip())
        # Should be roughly 2.0 seconds (+- 0.2s for container overhead/keyframes)
        assert 1.8 <= cut_duration <= 2.2
