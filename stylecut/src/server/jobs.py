"""Job manager and execution workers for StyleCut Engine."""
from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from src.captions import generate_ass, generate_srt, render_transparent_overlay
from src.hinglish import convert_transcript_to_hinglish
from src.llm import get_provider
from src.render_captions import create_captions_spec, render_captions_manifest
from src.server.models import CaptionJobResultResponse, JobProgressResponse, JobStatus
from src.transcribe import transcribe_clip
from src.utils import load_config, load_env

# Ensure environment variables are loaded for LLM providers
load_env()

logger = logging.getLogger("stylecut.engine")


def _extract_timeline_audio(
    media_path: Path,
    timeline_clips: list[dict[str, Any]],
    out_dir: Path,
    sequence_duration: float = 0.0,
    is_timeline_audio: bool = False,
) -> tuple[Path, float]:
    """Extract audio matching the active sequence timeline.

    Returns (audio_path, start_offset).
    """
    # Direct pre-rendered sequence timeline audio from Premiere Pro export
    if is_timeline_audio or (media_path.suffix.lower() == ".wav" and "timeline" in media_path.name.lower()):
        logger.info(f"Using pre-rendered sequence timeline audio mix: {media_path} ({media_path.stat().st_size} bytes)")
        return media_path, 0.0

    if not shutil.which("ffmpeg"):
        logger.warning("ffmpeg not found. Using raw media.")
        return media_path, 0.0

    target_wav = out_dir / "timeline_audio.wav"

    # Case 0: Empty timeline clips, but sequence duration is known (e.g. 37s)
    if not timeline_clips:
        if sequence_duration > 0.5:
            logger.info(f"No timeline_clips but sequence_duration={sequence_duration:.2f}s provided. Slicing raw media.")
            cmd = [
                "ffmpeg", "-y",
                "-i", str(media_path),
                "-t", f"{sequence_duration:.3f}",
                "-vn", "-ar", "16000", "-ac", "1",
                "-c:a", "pcm_s16le",
                str(target_wav)
            ]
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0 and target_wav.exists() and target_wav.stat().st_size > 500:
                logger.info(f"Extracted {target_wav.stat().st_size} bytes sequence audio.")
                return target_wav, 0.0
            else:
                logger.error(f"FFmpeg slice by sequence_duration failed: {res.stderr}")
        return media_path, 0.0

    # Case A: Single clip on timeline
    if len(timeline_clips) == 1:
        c = timeline_clips[0]
        in_p = float(c.get("in_point", 0.0))
        out_p = float(c.get("out_point", 0.0))
        start_t = float(c.get("start", 0.0))
        dur = float(c.get("duration", 0.0))
        if dur <= 0.0 and out_p > in_p:
            dur = out_p - in_p
        elif dur <= 0.0:
            dur = float(c.get("end", 0.0)) - start_t

        c_path = Path(c.get("media_path") or str(media_path))
        if not c_path.exists():
            c_path = media_path

        # If clip has duration, extract the exact timeline sub-clip
        if dur > 0.1:
            cmd = [
                "ffmpeg", "-y",
                "-ss", f"{in_p:.3f}",
                "-i", str(c_path),
                "-t", f"{dur:.3f}",
                "-vn", "-ar", "16000", "-ac", "1",
                "-c:a", "pcm_s16le",
                str(target_wav)
            ]
            logger.info(f"Extracting timeline audio slice ({in_p:.2f}s to {in_p+dur:.2f}s, dur={dur:.2f}s): {' '.join(cmd)}")
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0 and target_wav.exists() and target_wav.stat().st_size > 500:
                logger.info(f"Extracted {target_wav.stat().st_size} bytes timeline audio.")
                return target_wav, start_t
            else:
                logger.error(f"FFmpeg slice extraction failed: {res.stderr}")

        return media_path, start_t

    # Case B: Multiple clips/cuts on timeline
    cut_wavs: list[Path] = []
    first_start = float(timeline_clips[0].get("start", 0.0))
    for i, c in enumerate(timeline_clips):
        c_path = Path(c.get("media_path") or str(media_path))
        if not c_path.exists():
            c_path = media_path
        if not c_path.exists():
            continue

        in_p = float(c.get("in_point", 0.0))
        out_p = float(c.get("out_point", 0.0))
        dur = float(c.get("duration", 0.0))
        if dur <= 0.0 and out_p > in_p:
            dur = out_p - in_p
        elif dur <= 0.0:
            dur = float(c.get("end", 0.0)) - float(c.get("start", 0.0))

        if dur <= 0.05:
            continue

        part_wav = out_dir / f"cut_{i}.wav"
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{in_p:.3f}",
            "-i", str(c_path),
            "-t", f"{dur:.3f}",
            "-vn", "-ar", "16000", "-ac", "1",
            "-c:a", "pcm_s16le",
            str(part_wav)
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0 and part_wav.exists() and part_wav.stat().st_size > 500:
            cut_wavs.append(part_wav)

    if cut_wavs:
        concat_txt = out_dir / "concat_list.txt"
        with open(concat_txt, "w", encoding="utf-8") as f:
            for cw in cut_wavs:
                f.write(f"file '{cw.resolve()}'\n")
        concat_cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(concat_txt),
            "-c:a", "pcm_s16le", str(target_wav)
        ]
        res = subprocess.run(concat_cmd, capture_output=True, text=True)
        if res.returncode == 0 and target_wav.exists() and target_wav.stat().st_size > 500:
            logger.info(f"Assembled {len(cut_wavs)} cuts into timeline_audio.wav ({target_wav.stat().st_size} bytes)")
            return target_wav, first_start

    return media_path, 0.0


@dataclass
class JobRecord:
    job_id: str
    job_type: str
    params: dict[str, Any]
    status: JobStatus = JobStatus.QUEUED
    pct: int = 0
    step: str = "Queued"
    error: Optional[str] = None
    result: Optional[dict[str, Any]] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class JobManager:
    """Thread-safe in-memory job store with background worker pool."""

    def __init__(self, max_workers: int = 2) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="sc-worker")
        self._data_dir = Path.home() / ".stylecut"
        self._jobs_dir = self._data_dir / "jobs"
        self._cache_dir = self._data_dir / "cache"
        self._jobs_dir.mkdir(parents=True, exist_ok=True)
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def create_caption_job(self, params: dict[str, Any]) -> str:
        job_id = uuid.uuid4().hex[:12]
        rec = JobRecord(
            job_id=job_id,
            job_type="captions",
            params=params,
            status=JobStatus.QUEUED,
            pct=0,
            step="Job queued",
        )
        with self._lock:
            self._jobs[job_id] = rec

        # Dispatch to background thread pool
        self._pool.submit(self._run_caption_worker, job_id, params)
        return job_id

    def get_progress(self, job_id: str) -> Optional[JobProgressResponse]:
        with self._lock:
            rec = self._jobs.get(job_id)
            if not rec:
                return None
            return JobProgressResponse(
                job_id=rec.job_id,
                status=rec.status,
                pct=rec.pct,
                step=rec.step,
                error=rec.error,
            )

    def get_result(self, job_id: str) -> Optional[CaptionJobResultResponse]:
        with self._lock:
            rec = self._jobs.get(job_id)
            if not rec:
                return None
            res = rec.result or {}
            return CaptionJobResultResponse(
                job_id=rec.job_id,
                status=rec.status,
                srt_en=res.get("srt_en"),
                srt_hi=res.get("srt_hi"),
                ass=res.get("ass"),
                overlay_mov=res.get("overlay_mov"),
                manifest_path=res.get("manifest_path"),
                captions_spec=res.get("captions_spec"),
                clips=res.get("clips"),
                duration_sec=res.get("duration_sec", 0.0),
                segments_count=res.get("segments_count", 0),
                words_count=res.get("words_count", 0),
            )

    def _update_progress(self, job_id: str, pct: int, step: str) -> None:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].pct = pct
                self._jobs[job_id].step = step
                self._jobs[job_id].updated_at = time.time()
                if pct > 0 and self._jobs[job_id].status == JobStatus.QUEUED:
                    self._jobs[job_id].status = JobStatus.RUNNING

    def _set_completed(self, job_id: str, result: dict[str, Any]) -> None:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].status = JobStatus.COMPLETED
                self._jobs[job_id].pct = 100
                self._jobs[job_id].step = "Complete"
                self._jobs[job_id].result = result
                self._jobs[job_id].updated_at = time.time()

    def _set_failed(self, job_id: str, error_msg: str) -> None:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].status = JobStatus.FAILED
                self._jobs[job_id].error = error_msg
                self._jobs[job_id].step = "Failed"
                self._jobs[job_id].updated_at = time.time()

    def _run_caption_worker(self, job_id: str, params: dict[str, Any]) -> None:
        """Worker executing transcribe, Hinglish conversion, and SRT export."""
        try:
            self._update_progress(job_id, 5, "Validating input media")
            media_path = Path(params["media_path"])
            if not media_path.exists():
                raise FileNotFoundError(f"Media file not found: {media_path}")

            # Job specific output folder
            out_dir = self._jobs_dir / job_id
            out_dir.mkdir(parents=True, exist_ok=True)

            # Load project configuration if available
            repo_root = Path(__file__).parent.parent.parent
            cfg_path = repo_root / "config.yaml"
            cfg = load_config(cfg_path) if cfg_path.exists() else {}

            whisper_cfg = cfg.get("whisper", {})
            cap_cfg = cfg.get("captions", {})
            llm_cfg = cfg.get("llm", {})

            # Override with request parameters if provided
            model_name = params.get("model") or whisper_cfg.get("model", "small")
            lang = params.get("language") or whisper_cfg.get("language")
            words_per_line = params.get("words_per_line", cap_cfg.get("words_per_line", 3))
            max_lines = params.get("max_lines", cap_cfg.get("max_lines", 1))
            hinglish_enabled = params.get("hinglish", cap_cfg.get("hinglish_enabled", True))

            # ── 0. Sequence Timeline Audio Extraction ───────────────────────
            timeline_clips = params.get("timeline_clips") or []
            seq_dur = float(params.get("sequence_duration") or 0.0)
            is_timeline_audio = bool(params.get("is_timeline_audio", False))
            logger.info(f"[{job_id}] Received job (is_timeline_audio={is_timeline_audio}, seq_dur={seq_dur:.2f}s, clips={len(timeline_clips)}): {media_path}")
            self._update_progress(job_id, 10, "Extracting sequence timeline audio...")
            audio_to_transcribe, start_offset = _extract_timeline_audio(
                media_path,
                timeline_clips,
                out_dir,
                sequence_duration=seq_dur,
                is_timeline_audio=is_timeline_audio,
            )

            # ── 1. Transcription (Whisper) ──────────────────────────────────
            self._update_progress(job_id, 20, f"Loading Whisper model '{model_name}'...")
            logger.info(f"[{job_id}] Transcribing {audio_to_transcribe.name} with model={model_name} (offset={start_offset:.2f}s)")

            # Determine execution device safely
            w_device = whisper_cfg.get("device", "cpu")
            w_compute = whisper_cfg.get("compute_type", "int8")
            try:
                import torch
                if not torch.cuda.is_available():
                    w_device = "cpu"
                    w_compute = "int8"
            except Exception:
                w_device = "cpu"
                w_compute = "int8"

            def on_transcribe_progress(current_s: float, total_s: float) -> None:
                ratio = min(1.0, max(0.0, current_s / max(1.0, total_s)))
                p_pct = 35 + int(ratio * 30)
                cur_m, cur_s = int(current_s // 60), int(current_s % 60)
                tot_m, tot_s = int(total_s // 60), int(total_s % 60)
                p_step = f"Transcribing audio: {cur_m:02d}:{cur_s:02d} / {tot_m:02d}:{tot_s:02d} ({p_pct}%)"
                self._update_progress(job_id, p_pct, p_step)

            self._update_progress(job_id, 35, f"Transcribing audio with Whisper ({w_device.upper()})...")
            transcript = transcribe_clip(
                audio_to_transcribe,
                self._cache_dir / "transcripts",
                model=model_name,
                device=w_device,
                compute_type=w_compute,
                fallback_model=whisper_cfg.get("fallback_model", "base"),
                fallback_device=whisper_cfg.get("fallback_device", "cpu"),
                fallback_compute_type=whisper_cfg.get("fallback_compute_type", "int8"),
                language=lang,
                initial_prompt=whisper_cfg.get("initial_prompt"),
                progress_callback=on_transcribe_progress,
            )

            # Apply timeline start offset if the clip starts after 0 on timeline
            if start_offset > 0.05:
                for seg in transcript.get("segments", []):
                    seg["start"] = round(seg["start"] + start_offset, 3)
                    seg["end"] = round(seg["end"] + start_offset, 3)
                    for w in seg.get("words", []):
                        w["start"] = round(w["start"] + start_offset, 3)
                        w["end"] = round(w["end"] + start_offset, 3)
                for w in transcript.get("words", []):
                    w["start"] = round(w["start"] + start_offset, 3)
                    w["end"] = round(w["end"] + start_offset, 3)

            total_words = len(transcript.get("words", []))
            total_segments = len(transcript.get("segments", []))
            duration_sec = transcript.get("duration_sec", 0.0)

            # ── 2. Hinglish Conversion (LLM) ────────────────────────────────
            h_transcript = transcript
            if hinglish_enabled:
                self._update_progress(job_id, 65, "Translating Hindi/Hinglish to Roman script...")
                try:
                    llm = get_provider(llm_cfg, self._cache_dir / "llm")
                    h_transcript = convert_transcript_to_hinglish(transcript, llm, {})
                except Exception as e:
                    logger.warning(f"[{job_id}] Hinglish LLM step skipped or failed: {e}")
                    h_transcript = transcript

            template_preset = params.get("template", "hormozi")

            # ── 3. Generate SRT / ASS Subtitles ──────────────────────────────
            self._update_progress(job_id, 85, f"Generating SRT and ASS files ({template_preset} template)...")
            stem = media_path.stem
            en_srt_path = out_dir / f"{stem}_en.srt"
            hi_srt_path = out_dir / f"{stem}_hinglish.srt"
            ass_path = out_dir / f"{stem}_word_highlight.ass"

            # Generate English SRT
            generate_srt(
                transcript,
                en_srt_path,
                use_hinglish=False,
                words_per_line=words_per_line,
                max_lines=max_lines,
                template=template_preset,
            )

            # Generate Hinglish SRT
            generate_srt(
                h_transcript,
                hi_srt_path,
                use_hinglish=True,
                words_per_line=words_per_line,
                max_lines=max_lines,
                template=template_preset,
            )

            # Generate Styled ASS
            generate_ass(
                h_transcript,
                ass_path,
                font=cap_cfg.get("font", "Montserrat ExtraBold"),
                size=int(cap_cfg.get("size", 72)),
                highlight_color=cap_cfg.get("highlight_color", "&H0000D4FF"),
                text_color=cap_cfg.get("text_color", "&H00FFFFFF"),
                outline_color=cap_cfg.get("outline_color", "&H00000000"),
                outline_size=int(cap_cfg.get("outline_size", 4)),
                shadow=int(cap_cfg.get("shadow", 2)),
                position=cap_cfg.get("position", "lower_third"),
                words_per_line=words_per_line,
                max_lines=max_lines,
                style=cap_cfg.get("style", "word_popup"),
                use_hinglish=hinglish_enabled,
                template=template_preset,
            )

            # ── 4. Render Remotion Template Captions (Per-line clips & manifest) ─
            manifest_path_str = None
            manifest_clips = []
            captions_spec_data = None
            seq_w = int(params.get("sequence_width") or 1080)
            seq_h = int(params.get("sequence_height") or 1920)

            try:
                self._update_progress(job_id, 90, "Rendering Remotion template captions (hook_gold)...")
                remotion_template = params.get("template_id") or params.get("template") or "hook_gold"
                if remotion_template in ["hook", "hormozi", "viral", "default"]:
                    remotion_template = "hook_gold"

                canvas_cfg = {
                    "width": seq_w,
                    "height": seq_h,
                    "fps": 30,
                }
                captions_spec_data = create_captions_spec(
                    h_transcript,
                    template_id=remotion_template,
                    canvas=canvas_cfg,
                    style_overrides=params.get("style_overrides"),
                    words_per_line=words_per_line,
                    max_words_per_line=max_lines * words_per_line if max_lines else 4,
                    use_hinglish=hinglish_enabled,
                )
                total_lines_count = len(captions_spec_data.get("lines", []))
                def on_clip_progress(done_count: int, total_count: int) -> None:
                    p_pct = 90 + int((done_count / max(1, total_count)) * 9)
                    self._update_progress(
                        job_id,
                        p_pct,
                        f"Rendering caption clips: {done_count}/{total_count} ({p_pct}%)",
                    )

                manifest_res = render_captions_manifest(
                    captions_spec_data,
                    output_dir=out_dir / "remotion_clips",
                    concurrency=4,
                    progress_callback=on_clip_progress,
                )
                if manifest_res.get("manifest_path") and Path(manifest_res["manifest_path"]).exists():
                    manifest_path_str = manifest_res["manifest_path"]
                    manifest_clips = manifest_res.get("clips", [])
            except Exception as eRemotion:
                logger.warning(f"[{job_id}] Remotion template captions render failed: {eRemotion}", exc_info=True)

            # ── 4b. Fallback Monolithic Overlay (Only if Remotion failed) ───────
            mov_path = out_dir / f"{stem}_captions_overlay.mov"
            overlay_mov_str = None
            if not manifest_path_str:
                try:
                    self._update_progress(job_id, 98, "Rendering fallback video overlay...")
                    render_transparent_overlay(
                        ass_path,
                        mov_path,
                        duration=seq_dur if seq_dur > 0 else duration_sec,
                        width=seq_w,
                        height=seq_h,
                        fps=30
                    )
                    overlay_mov_str = str(mov_path.resolve())
                except Exception as eMov:
                    logger.warning(f"[{job_id}] Fallback overlay render skipped: {eMov}")

            # ── 5. Finalize ──────────────────────────────────────────────────
            result_payload = {
                "srt_en": str(en_srt_path.resolve()),
                "srt_hi": str(hi_srt_path.resolve()),
                "ass": str(ass_path.resolve()),
                "overlay_mov": overlay_mov_str,
                "manifest_path": manifest_path_str,
                "captions_spec": captions_spec_data,
                "clips": manifest_clips,
                "duration_sec": duration_sec,
                "segments_count": total_segments,
                "words_count": total_words,
            }
            logger.info(f"[{job_id}] Successfully generated captions: {result_payload}")
            self._set_completed(job_id, result_payload)

        except Exception as exc:
            logger.exception(f"[{job_id}] Caption job failed: {exc}")
            self._set_failed(job_id, str(exc))
