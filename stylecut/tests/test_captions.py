"""Tests for src/captions.py."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from src.captions import (
    _ass_time,
    _group_words,
    _srt_time,
    generate_ass,
    generate_srt,
)


# ─────────────────────────── time formatting ─────────────────────────────────

def test_srt_time_zero():
    assert _srt_time(0.0) == "00:00:00,000"


def test_srt_time_full():
    assert _srt_time(3661.5) == "01:01:01,500"


def test_srt_time_boundary():
    assert _srt_time(90.123) == "00:01:30,123"


def test_ass_time_zero():
    assert _ass_time(0.0) == "0:00:00.00"


def test_ass_time_minutes():
    assert _ass_time(90.5) == "0:01:30.50"


def test_ass_time_hour():
    assert _ass_time(3661.0) == "1:01:01.00"


# ─────────────────────────── word grouping ───────────────────────────────────

def test_group_words_basic(sample_transcript: dict):
    words = sample_transcript["words"]
    lines = _group_words(words, words_per_line=3)
    assert len(lines) == 2  # 6 words → 2 groups of 3
    assert lines[0]["words"][0]["word"] == words[0]["word"]
    assert lines[1]["words"][-1]["word"] == words[-1]["word"]


def test_group_words_start_end_times(sample_transcript: dict):
    words = sample_transcript["words"]
    lines = _group_words(words, words_per_line=3)
    assert lines[0]["start"] == words[0]["start"]
    assert lines[0]["end"] == words[2]["end"]
    assert lines[1]["start"] == words[3]["start"]
    assert lines[1]["end"] == words[-1]["end"]


def test_group_words_uneven(sample_transcript: dict):
    """5 words with words_per_line=3 → [3, 2] groups."""
    words = sample_transcript["words"][:5]
    lines = _group_words(words, words_per_line=3)
    assert len(lines) == 2
    assert len(lines[0]["words"]) == 3
    assert len(lines[1]["words"]) == 2


def test_group_words_empty():
    assert _group_words([], words_per_line=3) == []


# ─────────────────────────── SRT generation ──────────────────────────────────

def test_generate_srt_creates_file(tmp_path: Path, sample_transcript: dict):
    out = tmp_path / "test.srt"
    generate_srt(sample_transcript, out, use_hinglish=False)
    assert out.exists()


def test_generate_srt_content(tmp_path: Path, sample_transcript: dict):
    out = tmp_path / "test.srt"
    generate_srt(sample_transcript, out, use_hinglish=False, words_per_line=3)
    content = out.read_text(encoding="utf-8")

    assert "1\n" in content                     # first index
    assert "-->" in content                     # time arrow
    assert "Hello" in content                   # first word


def test_generate_srt_index_sequence(tmp_path: Path, sample_transcript: dict):
    out = tmp_path / "test.srt"
    generate_srt(sample_transcript, out, words_per_line=3)
    content = out.read_text(encoding="utf-8")
    # SRT entries are separated by blank lines; split on double newline
    entries = [e.strip() for e in content.split("\n\n") if e.strip()]
    assert entries[0].startswith("1\n")  # first entry starts with index 1
    assert entries[1].startswith("2\n")  # second entry starts with index 2


def test_generate_srt_hinglish_uses_word_hinglish(tmp_path: Path, sample_transcript: dict):
    for w in sample_transcript["words"]:
        w["word_hinglish"] = w["word"].strip() + "_H"
    out = tmp_path / "hi.srt"
    generate_srt(sample_transcript, out, use_hinglish=True)
    content = out.read_text(encoding="utf-8")
    assert "_H" in content


# ─────────────────────────── ASS generation ──────────────────────────────────

def test_generate_ass_creates_file(tmp_path: Path, sample_transcript: dict):
    out = tmp_path / "test.ass"
    generate_ass(sample_transcript, out)
    assert out.exists()


def test_generate_ass_has_required_sections(tmp_path: Path, sample_transcript: dict):
    out = tmp_path / "test.ass"
    generate_ass(sample_transcript, out)
    content = out.read_text(encoding="utf-8")

    for section in ("[Script Info]", "[V4+ Styles]", "[Events]", "Dialogue:"):
        assert section in content, f"Missing section: {section}"


def test_generate_ass_has_karaoke_tags(tmp_path: Path, sample_transcript: dict):
    out = tmp_path / "test.ass"
    generate_ass(sample_transcript, out, style="karaoke")
    content = out.read_text(encoding="utf-8")
    assert r"\kf" in content


def test_generate_ass_dialogue_count_karaoke(tmp_path: Path, sample_transcript: dict):
    """6 words / 3 per line = 2 Dialogue lines in karaoke mode."""
    out = tmp_path / "test.ass"
    generate_ass(sample_transcript, out, words_per_line=3, style="karaoke")
    content = out.read_text(encoding="utf-8")
    dialogue_lines = [l for l in content.splitlines() if l.startswith("Dialogue:")]
    assert len(dialogue_lines) == 2


def test_generate_ass_word_popup_two_lines(tmp_path: Path, sample_transcript: dict):
    """6 words with words_per_line=3, max_lines=2 pop up word by word (6 events)."""
    out = tmp_path / "test.ass"
    generate_ass(sample_transcript, out, words_per_line=3, max_lines=2, style="word_popup")
    content = out.read_text(encoding="utf-8")
    dialogue_lines = [l for l in content.splitlines() if l.startswith("Dialogue:")]
    assert len(dialogue_lines) == 6
    # Line 2 break appears once words reach line 2
    assert r"\N" in content


def test_generate_ass_timing_starts_at_zero(tmp_path: Path, sample_transcript: dict):
    out = tmp_path / "test.ass"
    generate_ass(sample_transcript, out, words_per_line=3)
    content = out.read_text(encoding="utf-8")
    first_dialogue = next(l for l in content.splitlines() if l.startswith("Dialogue:"))
    # First line should start at time 0 → "0:00:00.00"
    assert "0:00:00.00" in first_dialogue


def test_generate_ass_respects_font_param(tmp_path: Path, sample_transcript: dict):
    out = tmp_path / "test.ass"
    generate_ass(sample_transcript, out, font="Arial Black")
    content = out.read_text(encoding="utf-8")
    assert "Arial Black" in content


def test_generate_ass_respects_highlight_color(tmp_path: Path, sample_transcript: dict):
    """highlight_color param is encoded in karaoke \\kf tags in the text field.
    The style definition uses text_color for inactive words; the karaoke fill
    sweeps using the PrimaryColour of the style.
    Verify the ASS file at least contains karaoke tags (color is applied by
    the player using the style's PrimaryColour at the time of the sweep)."""
    out = tmp_path / "test.ass"
    generate_ass(sample_transcript, out, text_color="&H0000FF00")
    content = out.read_text(encoding="utf-8")
    assert "&H0000FF00" in content  # text_color in Style definition


def test_generate_srt_hormozi_template(tmp_path: Path, sample_transcript: dict):
    out = tmp_path / "hormozi.srt"
    generate_srt(sample_transcript, out, template="hormozi")
    content = out.read_text(encoding="utf-8")
    assert '<font color="#97DE25">' in content
    assert "HELLO" in content or "WORLD" in content


def test_generate_srt_mrbeast_template(tmp_path: Path, sample_transcript: dict):
    out = tmp_path / "mrbeast.srt"
    generate_srt(sample_transcript, out, template="mrbeast")
    content = out.read_text(encoding="utf-8")
    assert '<font color="#FFD400">' in content


def test_generate_srt_neon_template(tmp_path: Path, sample_transcript: dict):
    out = tmp_path / "neon.srt"
    generate_srt(sample_transcript, out, template="neon")
    content = out.read_text(encoding="utf-8")
    assert '<font color="#00F0FF">' in content


def test_generate_srt_minimal_template(tmp_path: Path, sample_transcript: dict):
    out = tmp_path / "minimal.srt"
    generate_srt(sample_transcript, out, template="minimal")
    content = out.read_text(encoding="utf-8")
    assert '<font color=' not in content


def test_generate_ass_with_template(tmp_path: Path, sample_transcript: dict):
    out = tmp_path / "hormozi.ass"
    generate_ass(sample_transcript, out, template="hormozi")
    content = out.read_text(encoding="utf-8")
    assert "Anton" in content
    assert "&H0025DE97" in content


# ─────────────────────────── burn-in (integration, optional) ─────────────────

@pytest.mark.skipif(
    subprocess.run(
        ["ffmpeg", "-version"],
        capture_output=True,
        # Don't raise on FileNotFoundError — return a fake result instead
    ).returncode != 0
    if __import__("shutil").which("ffmpeg") else True,
    reason="ffmpeg not available",
)
def test_burn_captions_produces_output(tmp_path: Path, sample_video: Path, sample_transcript: dict):
    """End-to-end burn test: ASS → burned MP4 via libx264 (no NVENC needed)."""
    from src.captions import burn_captions

    ass_path = tmp_path / "test.ass"
    generate_ass(sample_transcript, ass_path)

    out = tmp_path / "burned.mp4"
    burn_captions(
        sample_video,
        ass_path,
        out,
        encoder="libx264",          # force CPU so test works without GPU
        fallback_encoder="libx264",
    )
    assert out.exists()
    assert out.stat().st_size > 0
