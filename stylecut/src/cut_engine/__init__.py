"""Cut Engine v2 — Precision rough cut automation.

Features:
- Multi-format presets (reel, talking_head, podcast, vlog)
- VFR to CFR detection & audio preprocessing
- Silero VAD + adaptive noise-floor energy threshold
- WhisperX forced alignment with faster-whisper fallback
- Sentence segmentation, filler word & retake removal, gap shortening
- Zero-crossing snapping & acoustic safety padding
- Rational FPS frame-accurate cut list
- FCPXML / Premiere XML timeline delivery with markers
- 7-point automated QA suite with cut_report.md
- Parameter tightening / loosening with instant cache reuse
"""
from __future__ import annotations

from src.cut_engine.config import (
    CutEngineConfig,
    CutEnginePreset,
    PacingConfig,
    get_preset_config,
)
from src.cut_engine.engine import CutEngine, CutEngineResult

__all__ = [
    "CutEngine",
    "CutEngineConfig",
    "CutEnginePreset",
    "CutEngineResult",
    "PacingConfig",
    "get_preset_config",
]
