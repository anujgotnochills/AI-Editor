"""Tests for Cut Engine v2 (src/cut_engine/) — Precision Rough Cuts."""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from src.cut_engine.config import (
    CutEngineConfig,
    CutEnginePreset,
    PacingConfig,
    get_preset_config,
)
from src.cut_engine.decision import (
    WordToken,
    detect_fillers,
    detect_retakes,
    decide_keep_intervals,
    segment_into_clauses,
)
from src.cut_engine.engine import CutEngine
from src.cut_engine.preprocess import parse_fps
from src.cut_engine.qa import run_qa_checks, write_cut_report_md
from src.cut_engine.snapping import (
    find_lowest_energy_point,
    snap_to_zero_crossing,
    snap_cut_intervals,
)
from src.cut_engine.timeline import build_cut_list, time_to_frame, frame_to_time
from src.cut_engine.vad import compute_adaptive_energy_profile, detect_speech_regions
from src.cut_engine.xml_export import generate_premiere_fcpxml


# ─────────────────────────── 1. Config & Presets ─────────────────────────────

def test_config_presets():
    reel_pacing = get_preset_config(CutEnginePreset.REEL)
    podcast_pacing = get_preset_config(CutEnginePreset.PODCAST)
    talking_head_pacing = get_preset_config(CutEnginePreset.TALKING_HEAD)

    # Reel should be much tighter than Podcast
    assert reel_pacing.min_silence_sec < talking_head_pacing.min_silence_sec
    assert talking_head_pacing.min_silence_sec < podcast_pacing.min_silence_sec
    assert reel_pacing.keep_gap_ms < podcast_pacing.keep_gap_ms


def test_tighten_and_loosen_scaling():
    cfg = CutEngineConfig.from_dict(preset="talking_head")
    orig_silence = cfg.pacing.min_silence_sec
    orig_gap = cfg.pacing.keep_gap_ms

    cfg_tight = CutEngineConfig.from_dict(preset="talking_head", tighten=True)
    assert cfg_tight.pacing.min_silence_sec < orig_silence
    assert cfg_tight.pacing.keep_gap_ms < orig_gap
    assert cfg_tight.scale_factor < 1.0

    cfg_loose = CutEngineConfig.from_dict(preset="talking_head", loosen=True)
    assert cfg_loose.pacing.min_silence_sec > orig_silence
    assert cfg_loose.pacing.keep_gap_ms > orig_gap
    assert cfg_loose.scale_factor > 1.0


# ─────────────────────────── 2. Rational FPS & Frame Grid ────────────────────

def test_rational_fps_parsing():
    fps_val, fps_frac = parse_fps("30000/1001")
    assert round(fps_val, 2) == 29.97
    assert fps_frac == Fraction(30000, 1001)

    fps_val_24, fps_frac_24 = parse_fps("24000/1001")
    assert round(fps_val_24, 3) == 23.976
    assert fps_frac_24 == Fraction(24000, 1001)


def test_time_to_frame_exact():
    fps = Fraction(30, 1)
    assert time_to_frame(1.0, fps) == 30
    assert time_to_frame(2.5, fps) == 75

    # Round trip
    sec = frame_to_time(75, fps)
    assert sec == 2.5


# ─────────────────────────── 3. Adaptive Energy & VAD ────────────────────────

def test_adaptive_energy_profile():
    sr = 16000
    # 1 second of near-silence + 1 second of high-energy 440 Hz tone
    silence = np.random.normal(0, 0.001, sr).astype(np.float32)
    t = np.linspace(0, 1, sr, endpoint=False, dtype=np.float32)
    tone = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)

    audio = np.concatenate([silence, tone])
    energy_db, noise_floor, threshold = compute_adaptive_energy_profile(audio, sample_rate=sr, margin_db=10.0)

    # Noise floor should be well below speech threshold
    assert noise_floor < threshold
    assert threshold > -60.0


# ─────────────────────────── 4. Zero-Crossing Snapping ───────────────────────

def test_zero_crossing_snapping():
    sr = 16000
    t = np.linspace(0, 0.1, int(sr * 0.1), endpoint=False, dtype=np.float32)
    # Sine wave at 100 Hz has zero crossings every 5ms (80 samples)
    sine = (0.5 * np.sin(2 * np.pi * 100 * t)).astype(np.float32)

    # Sample at peak of wave (e.g. sample index 40 where sin is 1.0)
    peak_idx = 40
    snapped_idx = snap_to_zero_crossing(sine, sr, peak_idx, search_radius_samples=50)

    # At snapped index, amplitude should be extremely close to 0
    assert abs(sine[snapped_idx]) < 0.05
    # Or product with adjacent sample should be <= 0
    assert (sine[snapped_idx] * sine[min(len(sine) - 1, snapped_idx + 1)]) <= 0.05


# ─────────────────────────── 5. Decision: Retake & Fillers ───────────────────

def test_retake_detection():
    tokens = [
        WordToken("I", 1.0, 1.2),
        WordToken("think", 1.2, 1.5),
        WordToken("that", 1.5, 1.8),
        # restart
        WordToken("I", 2.5, 2.7),
        WordToken("think", 2.7, 3.0),
        WordToken("that", 3.0, 3.3),
        WordToken("this", 3.3, 3.6),
        WordToken("is", 3.6, 3.8),
        WordToken("great.", 3.8, 4.2),
    ]
    clauses = segment_into_clauses(tokens)
    assert len(clauses) >= 2

    classified = detect_retakes(clauses, similarity_threshold=0.70)
    assert classified[0].is_retake is True
    assert classified[1].is_retake is False


def test_filler_detection():
    tokens = [
        WordToken("Hello", 0.0, 0.5),
        WordToken("um,", 0.6, 0.8),
        WordToken("everyone", 0.9, 1.4),
        WordToken("matlab", 1.5, 1.8),
        WordToken("listen.", 1.9, 2.3),
    ]
    detected = detect_fillers(tokens, ["um", "matlab"], confidence_threshold=0.60)
    assert detected[1].is_filler is True
    assert detected[3].is_filler is True
    assert detected[0].is_filler is False


# ─────────────────────────── 6. End-to-End Pipeline on Synthetic Clip ─────────

def test_full_synthetic_cut_pipeline_and_qa(tmp_path: Path):
    sr = 16000
    # Generate 8 seconds of synthetic audio:
    # 0.0-1.0s: Silence
    # 1.0-2.0s: Speech 1 (False Start: "Today we will")
    # 2.0-2.8s: Silence
    # 2.8-3.1s: Filler ("um")
    # 3.1-3.6s: Silence
    # 3.6-5.5s: Speech 2 (Retake Kept: "Today we will make video")
    # 5.5-7.0s: 1.5s Long Silence (Gap to be shortened)
    # 7.0-8.0s: Speech 3 ("Thank you all.")
    dur = 8.5
    total_samples = int(sr * dur)
    audio = np.random.normal(0, 0.0005, total_samples).astype(np.float32)

    def add_tone(start_s, end_s, freq=300):
        s_i = int(start_s * sr)
        e_i = int(end_s * sr)
        t_arr = np.linspace(0, end_s - start_s, e_i - s_i, endpoint=False, dtype=np.float32)
        audio[s_i:e_i] += 0.4 * np.sin(2 * np.pi * freq * t_arr)

    add_tone(1.0, 2.0, 300)
    add_tone(2.8, 3.1, 250)
    add_tone(3.6, 5.5, 320)
    add_tone(7.0, 8.0, 310)

    # Save synthetic WAV
    wav_path = tmp_path / "synthetic_test.wav"
    sf.write(str(wav_path), audio, sr)

    # Synthetic transcript
    transcript = {
        "text": "Today we will um Today we will make video. Thank you all.",
        "duration_sec": dur,
        "words": [
            {"word": "Today", "start": 1.0, "end": 1.3, "probability": 0.95},
            {"word": "we", "start": 1.3, "end": 1.6, "probability": 0.95},
            {"word": "will", "start": 1.6, "end": 2.0, "probability": 0.95},
            {"word": "um,", "start": 2.8, "end": 3.1, "probability": 0.90},
            {"word": "Today", "start": 3.6, "end": 4.0, "probability": 0.95},
            {"word": "we", "start": 4.0, "end": 4.3, "probability": 0.95},
            {"word": "will", "start": 4.3, "end": 4.7, "probability": 0.95},
            {"word": "make", "start": 4.7, "end": 5.0, "probability": 0.95},
            {"word": "video.", "start": 5.0, "end": 5.5, "probability": 0.95},
            {"word": "Thank", "start": 7.0, "end": 7.3, "probability": 0.95},
            {"word": "you", "start": 7.3, "end": 7.6, "probability": 0.95},
            {"word": "all.", "start": 7.6, "end": 8.0, "probability": 0.95},
        ]
    }

    engine = CutEngine(cache_dir=tmp_path / "cache")
    result = engine.process(
        video_path=wav_path,
        transcript=transcript,
        output_dir=tmp_path / "output",
        render_video=False,
    )

    # 1. Assert Cut List
    assert result.cut_list_path.exists()
    cuts = result.cut_list["cuts"]
    assert len(cuts) >= 2
    summary = result.cut_list["summary"]
    assert summary["cut_duration_sec"] < summary["original_duration_sec"]
    assert summary["saved_percent"] > 25.0  # Significant reduction

    # 2. Assert Retake Discarded
    # Retake False start (1.0 - 2.0) should not be the dominant take
    assert any("retake" in str(c.get("reason", "")) or "silence" in str(c.get("reason", "")) for c in cuts)

    # 3. Assert XML sequence generated and valid XML
    assert result.xml_path.exists()
    xml_tree = ET.parse(result.xml_path)
    root = xml_tree.getroot()
    assert root.tag == "xmeml"
    seq = root.find(".//sequence")
    assert seq is not None

    # 4. Assert QA Suite and report
    assert result.report_path.exists()
    report_text = result.report_path.read_text(encoding="utf-8")
    assert "7-Point Quality Assurance" in report_text
    assert "Pacing Summary" in report_text
    assert result.qa_report.overall_status in ["PASS", "WARN"]

    # 5. Assert Remapped transcript in sync
    remapped = result.remapped_transcript
    assert remapped["duration_sec"] == round(summary["cut_duration_sec"], 3)


# ─────────────────────────── 7. Tighten vs Loosen Behavior ────────────────────

def test_tighten_vs_loosen_scaling(tmp_path: Path):
    sr = 16000
    dur = 6.0
    audio = np.random.normal(0, 0.001, int(sr * dur)).astype(np.float32)
    # 2 speech bursts separated by 1.5s silence
    t = np.linspace(0, 1.0, sr, endpoint=False, dtype=np.float32)
    audio[int(0.5 * sr):int(1.5 * sr)] += 0.4 * np.sin(2 * np.pi * 300 * t)
    audio[int(3.0 * sr):int(4.0 * sr)] += 0.4 * np.sin(2 * np.pi * 300 * t)

    wav_path = tmp_path / "tight_test.wav"
    sf.write(str(wav_path), audio, sr)

    transcript = {
        "text": "First part. Second part.",
        "duration_sec": dur,
        "words": [
            {"word": "First", "start": 0.5, "end": 1.0},
            {"word": "part.", "start": 1.0, "end": 1.5},
            {"word": "Second", "start": 3.0, "end": 3.5},
            {"word": "part.", "start": 3.5, "end": 4.0},
        ]
    }

    engine = CutEngine(cache_dir=tmp_path / "cache")

    res_tight = engine.process(
        video_path=wav_path,
        transcript=transcript,
        output_dir=tmp_path / "out_tight",
        tighten=True,
    )

    res_loose = engine.process(
        video_path=wav_path,
        transcript=transcript,
        output_dir=tmp_path / "out_loose",
        loosen=True,
    )

    # Tightened cut must be shorter or equal to loosened cut
    dur_tight = res_tight.cut_list["summary"]["cut_duration_sec"]
    dur_loose = res_loose.cut_list["summary"]["cut_duration_sec"]
    assert dur_tight <= dur_loose
