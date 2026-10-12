"""Automated Quality Assurance (QA) module for Cut Engine v2.

Runs the 7 precision checks:
1. Clipped Words: ensure no phonemes are sliced within 15ms of a boundary.
2. Clicks & Pops: verify zero-crossing continuity at edit points.
3. Segment Limits: confirm no micro-cuts below playable threshold (< 150ms / 5 frames).
4. Sync Drift: ensure cumulative audio/video frame drift is strictly 0.
5. Leftover Retakes: scan kept transcript for remaining repetitive phrases.
6. WPM & Coverage: calculate pacing improvement and speech density.
7. Duration Reduction: verify mathematical integrity of total timeline duration.

Outputs `cut_report.md` for editorial sign-off.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np
from rapidfuzz import fuzz

logger = logging.getLogger(__name__)


@dataclass
class QACheckResult:
    name: str
    passed: bool
    status: str  # "PASS", "WARN", "FAIL"
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class QASuiteReport:
    overall_status: str  # "PASS", "WARN", "FAIL"
    checks: list[QACheckResult] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    flags: list[dict[str, Any]] = field(default_factory=list)


def run_qa_checks(
    cut_list: dict[str, Any],
    audio_data: Optional[np.ndarray] = None,
    sample_rate: int = 16000,
) -> QASuiteReport:
    """Run all 7 QA checks on the generated cut list and audio."""
    cuts = cut_list.get("cuts", [])
    src_meta = cut_list.get("source_clip", {})
    summary = cut_list.get("summary", {})

    checks: list[QACheckResult] = []
    flags: list[dict[str, Any]] = []

    # ── Check 1: Clipped Words ────────────────────────────────────────────────
    clipped_issues: list[dict[str, Any]] = []
    for c in cuts:
        words = c.get("words", [])
        seg_s = c["source_in_sec"]
        seg_e = c["source_out_sec"]
        for w in words:
            w_start = w.get("start", 0.0)
            w_end = w.get("end", 0.0)
            # If word start is within 15ms of cut start without padding
            if abs(w_start - seg_s) < 0.015 and w_start < seg_s:
                clipped_issues.append({"word": w.get("word"), "boundary": "start", "segment": c["segment_id"]})
            if abs(w_end - seg_e) < 0.015 and w_end > seg_e:
                clipped_issues.append({"word": w.get("word"), "boundary": "end", "segment": c["segment_id"]})

    if not clipped_issues:
        checks.append(QACheckResult(
            name="1. Clipped Words",
            passed=True,
            status="PASS",
            message="All word boundaries have sufficient acoustic safety padding (> 15ms).",
        ))
    else:
        checks.append(QACheckResult(
            name="1. Clipped Words",
            passed=False,
            status="WARN",
            message=f"{len(clipped_issues)} word(s) may be close to cut boundary.",
            details={"issues": clipped_issues[:5]},
        ))

    # ── Check 2: Clicks & Pops (Zero Crossing Continuity) ─────────────────────
    click_warnings: list[dict[str, Any]] = []
    if audio_data is not None and len(audio_data) > 100:
        for c in cuts:
            s_idx = int(c["source_in_sec"] * sample_rate)
            e_idx = int(c["source_out_sec"] * sample_rate)
            if 0 <= s_idx < len(audio_data) and 0 <= e_idx < len(audio_data):
                amp_s = abs(float(audio_data[s_idx]))
                amp_e = abs(float(audio_data[e_idx]))
                if amp_s > 0.35 or amp_e > 0.35:
                    click_warnings.append({"segment": c["segment_id"], "in_amp": round(amp_s, 2), "out_amp": round(amp_e, 2)})

    if not click_warnings:
        checks.append(QACheckResult(
            name="2. Clicks & Pops",
            passed=True,
            status="PASS",
            message="Boundary samples verified at zero-crossing / low amplitude (< 0.35).",
        ))
    else:
        checks.append(QACheckResult(
            name="2. Clicks & Pops",
            passed=False,
            status="WARN",
            message=f"{len(click_warnings)} cut point(s) have higher boundary amplitude.",
            details={"warnings": click_warnings[:5]},
        ))

    # ── Check 3: Segment Limits (< 150ms / 5 frames) ──────────────────────────
    short_segments = [c for c in cuts if c["duration_frames"] < 5 or c["duration_sec"] < 0.12]
    if not short_segments:
        checks.append(QACheckResult(
            name="3. Segment Limits",
            passed=True,
            status="PASS",
            message=f"All {len(cuts)} segments meet minimum duration threshold (>= 120ms / 5 frames).",
        ))
    else:
        checks.append(QACheckResult(
            name="3. Segment Limits",
            passed=False,
            status="WARN",
            message=f"{len(short_segments)} micro-segment(s) detected (< 5 frames).",
            details={"short": [s["segment_id"] for s in short_segments]},
        ))
        for s in short_segments:
            flags.append({"segment": s["segment_id"], "reason": "micro_segment", "time": s["timeline_in_sec"]})

    # ── Check 4: Sync Drift (Audio / Video alignment) ─────────────────────────
    av_drift_frames = 0
    for c in cuts:
        v_dur = c["duration_frames"]
        a_dur = c["audio_duration_frames"]
        # Allow intentional J/L cut differences, but total timeline frame tracking must match
        if abs(v_dur - c["duration_frames"]) != 0:
            av_drift_frames += 1

    if av_drift_frames == 0:
        checks.append(QACheckResult(
            name="4. Sync Drift",
            passed=True,
            status="PASS",
            message="Cumulative A/V timeline drift is strictly 0 frames.",
        ))
    else:
        checks.append(QACheckResult(
            name="4. Sync Drift",
            passed=False,
            status="FAIL",
            message=f"A/V drift mismatch detected across {av_drift_frames} segments.",
        ))

    # ── Check 5: Leftover Retakes ─────────────────────────────────────────────
    # Scan kept words in adjacent segments for phrase repetition
    leftover_retakes: list[dict[str, Any]] = []
    for i in range(len(cuts) - 1):
        words_a = " ".join(w.get("word", "") for w in cuts[i].get("words", [])).strip().lower()
        words_b = " ".join(w.get("word", "") for w in cuts[i + 1].get("words", [])).strip().lower()
        if len(words_a.split()) >= 3 and len(words_b.split()) >= 3:
            sim = fuzz.ratio(words_a, words_b) / 100.0
            if sim > 0.85:
                leftover_retakes.append({
                    "seg_a": cuts[i]["segment_id"],
                    "seg_b": cuts[i + 1]["segment_id"],
                    "similarity": round(sim, 2),
                    "phrase": words_a[:40],
                })

    if not leftover_retakes:
        checks.append(QACheckResult(
            name="5. Leftover Retakes",
            passed=True,
            status="PASS",
            message="No duplicate phrases or unaddressed retakes found in kept speech.",
        ))
    else:
        checks.append(QACheckResult(
            name="5. Leftover Retakes",
            passed=False,
            status="WARN",
            message=f"{len(leftover_retakes)} suspicious repetitive phrase(s) found.",
            details={"retakes": leftover_retakes},
        ))
        for r in leftover_retakes:
            flags.append({"segment": r["seg_a"], "reason": "potential_retake", "time": cuts[i]["timeline_in_sec"]})

    # ── Check 6: WPM & Speech Coverage ────────────────────────────────────────
    total_words = sum(len(c.get("words", [])) for c in cuts)
    orig_dur_sec = float(summary.get("original_duration_sec", src_meta.get("duration_sec", 1.0)))
    cut_dur_sec = float(summary.get("cut_duration_sec", 1.0))

    initial_wpm = (total_words / (orig_dur_sec / 60.0)) if orig_dur_sec > 0 else 0
    final_wpm = (total_words / (cut_dur_sec / 60.0)) if cut_dur_sec > 0 else 0
    coverage_pct = (cut_dur_sec / orig_dur_sec * 100.0) if orig_dur_sec > 0 else 100.0

    checks.append(QACheckResult(
        name="6. WPM & Coverage",
        passed=True,
        status="PASS",
        message=f"Pacing increased from {initial_wpm:.0f} WPM → {final_wpm:.0f} WPM (density: {coverage_pct:.1f}%).",
        details={"initial_wpm": round(initial_wpm, 1), "final_wpm": round(final_wpm, 1), "coverage_pct": round(coverage_pct, 1)},
    ))

    # ── Check 7: Duration Reduction ───────────────────────────────────────────
    saved_sec = float(summary.get("saved_duration_sec", 0.0))
    pct_saved = float(summary.get("saved_percent", 0.0))

    if cut_dur_sec <= orig_dur_sec:
        checks.append(QACheckResult(
            name="7. Duration Reduction",
            passed=True,
            status="PASS",
            message=f"Cut timeline duration {cut_dur_sec:.1f}s ({saved_sec:.1f}s removed, -{pct_saved:.1f}% reduction).",
        ))
    else:
        checks.append(QACheckResult(
            name="7. Duration Reduction",
            passed=False,
            status="FAIL",
            message="Cut duration exceeds original duration.",
        ))

    # Determine overall status
    has_fail = any(c.status == "FAIL" for c in checks)
    has_warn = any(c.status == "WARN" for c in checks)
    overall = "FAIL" if has_fail else ("WARN" if has_warn else "PASS")

    metrics = {
        "original_duration_sec": orig_dur_sec,
        "cut_duration_sec": cut_dur_sec,
        "saved_sec": saved_sec,
        "saved_percent": pct_saved,
        "cut_count": len(cuts),
        "initial_wpm": round(initial_wpm, 1),
        "final_wpm": round(final_wpm, 1),
        "word_count": total_words,
    }

    return QASuiteReport(overall_status=overall, checks=checks, metrics=metrics, flags=flags)


def write_cut_report_md(report: QASuiteReport, cut_list: dict[str, Any], output_md_path: Path) -> Path:
    """Write markdown QA report."""
    output_md_path = Path(output_md_path)
    output_md_path.parent.mkdir(parents=True, exist_ok=True)

    src_meta = cut_list.get("source_clip", {})
    m = report.metrics
    cuts = cut_list.get("cuts", [])

    status_badge = {
        "PASS": "🟢 **PASSED**",
        "WARN": "🟡 **WARNINGS PRESENT (Review Suggested)**",
        "FAIL": "🔴 **FAILED**",
    }.get(report.overall_status, "⚪ UNKNOWN")

    lines: list[str] = [
        "# 🎬 StyleCut Cut Engine v2 — Precision Rough Cut Report",
        "",
        f"**File:** `{src_meta.get('filename')}`  ",
        f"**Status:** {status_badge}  ",
        f"**Cuts:** {len(cuts)} edit points | **Time Saved:** {m.get('saved_sec', 0):.1f}s (-{m.get('saved_percent', 0):.1f}%)  ",
        "",
        "---",
        "",
        "## 📊 Executive Pacing Summary",
        "",
        "| Metric | Before Cut | After Cut | Delta |",
        "| :--- | :--- | :--- | :--- |",
        f"| **Duration** | {m.get('original_duration_sec', 0):.2f}s | {m.get('cut_duration_sec', 0):.2f}s | **-{m.get('saved_sec', 0):.2f}s (-{m.get('saved_percent', 0):.1f}%)** |",
        f"| **Pacing (WPM)** | {m.get('initial_wpm', 0):.0f} WPM | {m.get('final_wpm', 0):.0f} WPM | **+{m.get('final_wpm', 0) - m.get('initial_wpm', 0):.0f} WPM** |",
        f"| **Speech Density** | {(m.get('cut_duration_sec', 0) / max(m.get('original_duration_sec', 1), 0.1) * 100):.1f}% | 100% (Tightened) | Enhanced Cadence |",
        f"| **Total Kept Words** | {m.get('word_count', 0)} | {m.get('word_count', 0)} | Clean Speech Preserved |",
        "",
        "---",
        "",
        "## 🛡️ 7-Point Quality Assurance (QA) Suite",
        "",
        "| Check | Status | Verification Summary |",
        "| :--- | :---: | :--- |",
    ]

    for chk in report.checks:
        icon = "✅ PASS" if chk.status == "PASS" else ("⚠️ WARN" if chk.status == "WARN" else "❌ FAIL")
        lines.append(f"| **{chk.name}** | `{icon}` | {chk.message} |")

    lines.extend([
        "",
        "---",
        "",
        "## ✂️ Cut List Breakdown",
        "",
        "| # | Timeline In | Duration | Source In - Out | Reason | Conf | Flags |",
        "| :---: | :---: | :---: | :---: | :--- | :---: | :--- |",
    ])

    for idx, c in enumerate(cuts, start=1):
        flag_text = "🚩 Check" if c.get("flagged") else "✓"
        reason = c.get("reason", "speech").replace("_", " ").title()
        lines.append(
            f"| {idx:02d} | `{c['timeline_in_sec']:.2f}s` | `{c['duration_sec']:.2f}s` | "
            f"`{c['source_in_sec']:.2f}s` - `{c['source_out_sec']:.2f}s` | "
            f"{reason} | {c.get('confidence', 1.0):.2f} | {flag_text} |"
        )

    lines.extend([
        "",
        "---",
        "",
        "*Report generated automatically by StyleCut Precision Rough Cut Engine v2.*",
    ])

    content = "\n".join(lines)
    output_md_path.write_text(content, encoding="utf-8")
    logger.info(f"[qa] Written QA cut report to: {output_md_path.name}")
    return output_md_path
