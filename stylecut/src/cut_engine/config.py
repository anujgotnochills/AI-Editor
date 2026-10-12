"""Configuration models and preset definitions for Cut Engine v2."""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field


class CutEnginePreset(str, Enum):
    REEL = "reel"
    TALKING_HEAD = "talking_head"
    PODCAST = "podcast"
    VLOG = "vlog"
    CUSTOM = "custom"


DEFAULT_FILLERS: list[str] = [
    "um",
    "uh",
    "umm",
    "uhh",
    "matlab",
    "basically",
    "like",
    "you know",
    "actually",
    "toh",
    "yaani",
    "literally",
    "i mean",
]


class PacingConfig(BaseModel):
    """Pacing and acoustic boundary tuning parameters."""
    min_silence_sec: float = Field(
        default=0.25,
        ge=0.05,
        le=5.0,
        description="Minimum pause duration in seconds to trigger a cut.",
    )
    keep_gap_ms: float = Field(
        default=120.0,
        ge=0.0,
        le=1000.0,
        description="Natural speech pause to retain between sentences/clauses.",
    )
    padding_before_ms: float = Field(
        default=60.0,
        ge=10.0,
        le=500.0,
        description="Safety cushion before speech to protect consonant attacks.",
    )
    padding_after_ms: float = Field(
        default=60.0,
        ge=10.0,
        le=500.0,
        description="Safety cushion after speech to protect vowel decays.",
    )
    snap_window_ms: float = Field(
        default=25.0,
        ge=5.0,
        le=100.0,
        description="Search window around cut boundary for lowest energy sample.",
    )
    crossfade_ms: float = Field(
        default=8.0,
        ge=0.0,
        le=50.0,
        description="Equal-power audio crossfade length at edit points.",
    )
    noise_floor_margin_db: float = Field(
        default=10.0,
        ge=2.0,
        le=30.0,
        description="dB margin above dynamic noise floor for energy gating.",
    )
    vad_threshold: float = Field(
        default=0.5,
        ge=0.1,
        le=0.95,
        description="Silero VAD speech probability threshold.",
    )
    remove_fillers: bool = Field(
        default=True,
        description="Whether to excise filler words.",
    )
    fillers: list[str] = Field(
        default_factory=lambda: list(DEFAULT_FILLERS),
        description="Vocabulary of filler words/phrases to remove.",
    )
    filler_confidence_threshold: float = Field(
        default=0.70,
        ge=0.0,
        le=1.0,
        description="Confidence threshold for automatic filler cutting.",
    )
    remove_retakes: bool = Field(
        default=True,
        description="Detect and cut false starts / repeated takes.",
    )
    retake_similarity_threshold: float = Field(
        default=0.72,
        ge=0.4,
        le=1.0,
        description="Fuzzy string similarity threshold to flag retakes.",
    )
    preserve_dramatic_pauses: bool = Field(
        default=True,
        description="Preserve pauses flagged with [pause] or dramatic emphasis.",
    )
    min_segment_duration_ms: float = Field(
        default=150.0,
        ge=50.0,
        le=1000.0,
        description="Minimum duration of any kept clip segment to avoid micro-cuts.",
    )
    j_cut_offset_ms: float = Field(
        default=0.0,
        ge=-200.0,
        le=200.0,
        description="Audio leads video by this offset (J-cut).",
    )
    l_cut_offset_ms: float = Field(
        default=0.0,
        ge=-200.0,
        le=200.0,
        description="Audio trails video by this offset (L-cut).",
    )


# ─────────────────────────── Pre-configured Presets ──────────────────────────

PRESET_CONFIGS: dict[CutEnginePreset, PacingConfig] = {
    CutEnginePreset.REEL: PacingConfig(
        min_silence_sec=0.18,
        keep_gap_ms=60.0,
        padding_before_ms=40.0,
        padding_after_ms=40.0,
        crossfade_ms=5.0,
        noise_floor_margin_db=8.0,
        vad_threshold=0.48,
        remove_fillers=True,
        filler_confidence_threshold=0.65,
        remove_retakes=True,
        min_segment_duration_ms=120.0,
    ),
    CutEnginePreset.TALKING_HEAD: PacingConfig(
        min_silence_sec=0.25,
        keep_gap_ms=120.0,
        padding_before_ms=60.0,
        padding_after_ms=60.0,
        crossfade_ms=8.0,
        noise_floor_margin_db=10.0,
        vad_threshold=0.50,
        remove_fillers=True,
        filler_confidence_threshold=0.70,
        remove_retakes=True,
        min_segment_duration_ms=150.0,
    ),
    CutEnginePreset.PODCAST: PacingConfig(
        min_silence_sec=0.40,
        keep_gap_ms=220.0,
        padding_before_ms=90.0,
        padding_after_ms=90.0,
        crossfade_ms=12.0,
        noise_floor_margin_db=12.0,
        vad_threshold=0.52,
        remove_fillers=True,
        filler_confidence_threshold=0.80,
        remove_retakes=True,
        min_segment_duration_ms=200.0,
    ),
    CutEnginePreset.VLOG: PacingConfig(
        min_silence_sec=0.32,
        keep_gap_ms=160.0,
        padding_before_ms=70.0,
        padding_after_ms=70.0,
        crossfade_ms=10.0,
        noise_floor_margin_db=11.0,
        vad_threshold=0.50,
        remove_fillers=True,
        filler_confidence_threshold=0.75,
        remove_retakes=True,
        min_segment_duration_ms=180.0,
    ),
}


def get_preset_config(preset: CutEnginePreset | str) -> PacingConfig:
    """Return a fresh copy of the pacing configuration for a preset."""
    if isinstance(preset, str):
        try:
            preset = CutEnginePreset(preset.lower())
        except ValueError:
            preset = CutEnginePreset.TALKING_HEAD

    base = PRESET_CONFIGS.get(preset, PRESET_CONFIGS[CutEnginePreset.TALKING_HEAD])
    return base.model_copy(deep=True)


class CutEngineConfig(BaseModel):
    """Full top-level Cut Engine configuration with scaling and client overrides."""
    preset: CutEnginePreset = CutEnginePreset.TALKING_HEAD
    pacing: PacingConfig = Field(default_factory=lambda: get_preset_config(CutEnginePreset.TALKING_HEAD))
    scale_factor: float = Field(default=1.0, ge=0.4, le=2.5)
    target_fps: Optional[float] = None
    force_cfr: bool = True
    export_xml: bool = True
    export_json: bool = True
    render_video: bool = True

    @classmethod
    def from_dict(
        cls,
        data: Optional[dict[str, Any]] = None,
        client_overrides: Optional[dict[str, Any]] = None,
        preset: Optional[str] = None,
        tighten: bool = False,
        loosen: bool = False,
    ) -> "CutEngineConfig":
        """Build a CutEngineConfig from config dicts, presets, and scaling flags."""
        raw_preset = preset or (data or {}).get("preset") or "talking_head"
        try:
            enum_preset = CutEnginePreset(str(raw_preset).lower())
        except ValueError:
            enum_preset = CutEnginePreset.TALKING_HEAD

        pacing = get_preset_config(enum_preset)

        # Merge base config
        if data:
            cut_engine_block = data.get("cut_engine") or data.get("pacing") or {}
            if isinstance(cut_engine_block, dict):
                # Update pacing fields if present
                for k, v in cut_engine_block.items():
                    if hasattr(pacing, k) and v is not None:
                        setattr(pacing, k, v)

        # Merge client profile overrides
        if client_overrides:
            c_block = (
                client_overrides.get("cut_engine")
                or client_overrides.get("pacing")
                or {}
            )
            if isinstance(c_block, dict):
                for k, v in c_block.items():
                    if hasattr(pacing, k) and v is not None:
                        setattr(pacing, k, v)

        cfg = cls(preset=enum_preset, pacing=pacing)

        # Apply tighten / loosen flags
        if tighten and loosen:
            pass  # cancel each other out
        elif tighten:
            cfg.apply_tighten()
        elif loosen:
            cfg.apply_loosen()

        return cfg

    def apply_tighten(self, factor: float = 0.75) -> None:
        """Scale min_silence_sec, keep_gap_ms, and padding tighter."""
        self.scale_factor = round(self.scale_factor * factor, 3)
        self.pacing.min_silence_sec = max(0.08, round(self.pacing.min_silence_sec * factor, 3))
        self.pacing.keep_gap_ms = max(20.0, round(self.pacing.keep_gap_ms * factor, 1))
        self.pacing.padding_before_ms = max(20.0, round(self.pacing.padding_before_ms * factor, 1))
        self.pacing.padding_after_ms = max(20.0, round(self.pacing.padding_after_ms * factor, 1))

    def apply_loosen(self, factor: float = 1.35) -> None:
        """Scale min_silence_sec, keep_gap_ms, and padding looser."""
        self.scale_factor = round(self.scale_factor * factor, 3)
        self.pacing.min_silence_sec = min(2.0, round(self.pacing.min_silence_sec * factor, 3))
        self.pacing.keep_gap_ms = min(600.0, round(self.pacing.keep_gap_ms * factor, 1))
        self.pacing.padding_before_ms = min(200.0, round(self.pacing.padding_before_ms * factor, 1))
        self.pacing.padding_after_ms = min(200.0, round(self.pacing.padding_after_ms * factor, 1))
