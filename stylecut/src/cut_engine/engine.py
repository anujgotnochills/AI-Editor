"""Cut Engine v2 main pipeline orchestrator.

Integrates:
1. Config & Presets
2. Preprocess (VFR->CFR, 16kHz mono audio)
3. Detection (Silero VAD + adaptive energy)
4. Alignment (WhisperX / faster-whisper fallback)
5. Decision (Clause segmentation, filler cut, retake removal, gap shortening, dramatic pauses)
6. Snapping (Lowest energy & zero-crossing snapping)
7. Timeline (Rational FPS frame grid, cut_list.json)
8. Delivery (FCPXML / Premiere XML with linked A/V, crossfades, markers)
9. QA Suite (7-point check & cut_report.md)
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import numpy as np
import soundfile as sf

from src.cut_engine.aligner import align_transcript
from src.cut_engine.config import CutEngineConfig, CutEnginePreset
from src.cut_engine.decision import KeepInterval, decide_keep_intervals
from src.cut_engine.preprocess import convert_to_cfr_if_needed, extract_analysis_audio, probe_media, parse_fps
from src.cut_engine.qa import QASuiteReport, run_qa_checks, write_cut_report_md
from src.cut_engine.snapping import snap_cut_intervals
from src.cut_engine.timeline import build_cut_list
from src.cut_engine.vad import detect_speech_regions
from src.cut_engine.xml_export import generate_premiere_fcpxml
from src.cut_engine.renderer import remap_transcript_to_cuts, render_rough_cut

logger = logging.getLogger(__name__)


@dataclass
class CutEngineResult:
    cut_list: dict[str, Any]
    cut_list_path: Path
    xml_path: Path
    report_path: Path
    qa_report: QASuiteReport
    keep_segments: list[dict[str, float]]
    remapped_transcript: dict[str, Any]
    output_video_path: Optional[Path] = None


class CutEngine:
    """Precision rough cut automation engine."""

    def __init__(
        self,
        config: Optional[CutEngineConfig] = None,
        cache_dir: Optional[Path] = None,
    ) -> None:
        self.config = config or CutEngineConfig()
        self.cache_dir = Path(cache_dir) if cache_dir else (Path.home() / ".stylecut" / "cache" / "cut_engine")
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_key(self, path: Path) -> str:
        st = path.stat()
        raw = f"{path.resolve()}:{st.st_size}:{st.st_mtime_ns}"
        return hashlib.md5(raw.encode()).hexdigest()

    def process(
        self,
        video_path: Path,
        transcript: dict[str, Any],
        output_dir: Path,
        *,
        tighten: bool = False,
        loosen: bool = False,
        render_video: bool = False,
        sequence_name: str = "",
    ) -> CutEngineResult:
        """Run the full precision rough cut pipeline on a video clip."""
        video_path = Path(video_path)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Apply tighten / loosen scaling if requested
        cfg = self.config.model_copy(deep=True)
        if tighten:
            cfg.apply_tighten()
            logger.info(f"[cut_engine] Applied --tighten (scale={cfg.scale_factor})")
        elif loosen:
            cfg.apply_loosen()
            logger.info(f"[cut_engine] Applied --loosen (scale={cfg.scale_factor})")

        pacing = cfg.pacing
        key = self._cache_key(video_path)
        stem = video_path.stem

        # ── 1. Preprocess: Probe & CFR conversion ─────────────────────────────
        logger.info(f"─── Step 1/6: Preprocessing & VFR Check: {video_path.name} ───")
        cfr_video_path = self.cache_dir / f"{key}_cfr.mp4"
        working_video_path, was_converted = convert_to_cfr_if_needed(
            video_path, cfr_video_path, target_fps=cfg.target_fps, force=False
        )

        probe = probe_media(working_video_path)
        streams = probe.get("streams", [])
        fmt = probe.get("format", {})
        video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
        audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), {})

        width = int(video_stream.get("width", 1080))
        height = int(video_stream.get("height", 1920))
        duration_sec = float(fmt.get("duration") or video_stream.get("duration") or transcript.get("duration_sec", 0.0))
        r_rate = video_stream.get("r_frame_rate", "30/1")
        avg_rate = video_stream.get("avg_frame_rate", "30/1")
        float_fps, frac_fps = parse_fps(r_rate, avg_rate)

        # ── 2. Preprocess: Extract 16 kHz mono WAV ────────────────────────────
        analysis_wav = self.cache_dir / f"{key}_16k.wav"
        extract_analysis_audio(working_video_path, analysis_wav, sample_rate=16000)

        # Load waveform for energy profiling and zero-crossing snapping
        audio_data, sr = sf.read(str(analysis_wav), dtype="float32")
        if audio_data.ndim > 1:
            audio_data = np.mean(audio_data, axis=1)

        # ── 3. VAD & Energy Region Detection (Cached) ─────────────────────────
        logger.info("─── Step 2/6: Silero VAD + Adaptive Energy Floor ───")
        vad_cache_file = self.cache_dir / f"{key}_vad.json"
        if vad_cache_file.exists():
            speech_regions = json.loads(vad_cache_file.read_text(encoding="utf-8"))
        else:
            speech_regions = detect_speech_regions(
                analysis_wav,
                vad_threshold=pacing.vad_threshold,
                noise_floor_margin_db=pacing.noise_floor_margin_db,
            )
            vad_cache_file.write_text(json.dumps(speech_regions, indent=2), encoding="utf-8")

        # ── 4. Word Alignment (Cached) ────────────────────────────────────────
        logger.info("─── Step 3/6: Word Alignment & Boundary Protection ───")
        align_cache_file = self.cache_dir / f"{key}_aligned_words.json"
        if align_cache_file.exists():
            aligned_words = json.loads(align_cache_file.read_text(encoding="utf-8"))
        else:
            aligned_words = align_transcript(analysis_wav, transcript)
            align_cache_file.write_text(json.dumps(aligned_words, indent=2), encoding="utf-8")

        # ── 5. Decision Engine ────────────────────────────────────────────────
        logger.info(
            f"─── Step 4/6: Decision Engine (Preset: {cfg.preset.value}, "
            f"min_silence={pacing.min_silence_sec}s, keep_gap={pacing.keep_gap_ms}ms) ───"
        )
        keep_intervals = decide_keep_intervals(
            aligned_words,
            clip_duration=duration_sec,
            pacing=pacing,
            raw_transcript=transcript,
            speech_regions=speech_regions,
        )

        # ── 6. Acoustic Snapping to Zero-Crossing ─────────────────────────────
        logger.info(f"─── Step 5/6: Acoustic Snapping (±{pacing.snap_window_ms}ms window) ───")
        snapped_intervals = snap_cut_intervals(
            audio_data,
            sample_rate=sr,
            intervals=keep_intervals,
            snap_window_ms=pacing.snap_window_ms,
            clip_duration=duration_sec,
        )

        # ── 7. Rational Frame Grid & Cut List ─────────────────────────────────
        cut_list = build_cut_list(
            snapped_intervals,
            source_clip_path=working_video_path,
            fps=frac_fps,
            clip_duration_sec=duration_sec,
            pacing=pacing,
            width=width,
            height=height,
            sample_rate=int(audio_stream.get("sample_rate", 48000)),
        )

        cut_list_path = output_dir / f"{stem}_cut_list.json"
        cut_list_path.write_text(json.dumps(cut_list, indent=2, ensure_ascii=False), encoding="utf-8")

        # ── 8. Timeline Delivery: FCPXML / Premiere XML ───────────────────────
        xml_path = output_dir / f"{stem}_sequence.xml"
        generate_premiere_fcpxml(
            cut_list, output_xml_path=xml_path, sequence_name=sequence_name or f"{stem} — StyleCut Rough Cut"
        )

        # ── 9. QA 7-Point Verification Suite ──────────────────────────────────
        logger.info("─── Step 6/6: Automated QA Suite & Editorial Report ───")
        qa_report = run_qa_checks(cut_list, audio_data=audio_data, sample_rate=sr)
        report_path = output_dir / f"{stem}_cut_report.md"
        write_cut_report_md(qa_report, cut_list, output_md_path=report_path)

        # ── 10. Backward-compatible Keep Segments & Transcript Remapping ───────
        compat_keep_segments = [
            {"start": c["source_in_sec"], "end": c["source_out_sec"], "duration": c["duration_sec"]}
            for c in cut_list["cuts"]
        ]
        remapped_transcript = remap_transcript_to_cuts(transcript, compat_keep_segments)

        # ── 11. Optional Video Render ─────────────────────────────────────────
        rendered_path: Optional[Path] = None
        if render_video or cfg.render_video:
            out_mp4 = output_dir / f"{stem}_rough_cut.mp4"
            logger.info(f"[cut_engine] Rendering rough cut video with FFmpeg: {out_mp4.name}")
            try:
                render_rough_cut(
                    video_path=working_video_path,
                    keep_segments=compat_keep_segments,
                    output_path=out_mp4,
                    has_audio=bool(audio_stream),
                )
                rendered_path = out_mp4
            except Exception as r_err:
                logger.warning(f"[cut_engine] FFmpeg rough cut render notice: {r_err}")

        logger.info(
            f"🎉 Rough cut complete: {len(cut_list['cuts'])} cuts | "
            f"{cut_list['summary']['original_duration_sec']}s → {cut_list['summary']['cut_duration_sec']}s "
            f"(-{cut_list['summary']['saved_percent']}%) | Status: {qa_report.overall_status}"
        )

        return CutEngineResult(
            cut_list=cut_list,
            cut_list_path=cut_list_path,
            xml_path=xml_path,
            report_path=report_path,
            qa_report=qa_report,
            keep_segments=compat_keep_segments,
            remapped_transcript=remapped_transcript,
            output_video_path=rendered_path,
        )
