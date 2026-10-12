"""FastAPI application for StyleCut local engine."""
from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from src.utils import load_env

# Ensure environment variables (.env) are loaded
load_env()

from src.render_captions import render_single_line
from src.server.jobs import JobManager
from src.server.models import (
    CaptionEditRequest,
    CaptionJobRequest,
    CaptionJobResultResponse,
    HealthResponse,
    JobCreationResponse,
    JobProgressResponse,
    TemplateInfo,
)

logger = logging.getLogger("stylecut.engine")

app = FastAPI(
    title="StyleCut Engine API",
    version="0.1.0",
    description="Local background engine for Premiere Pro StyleCut Extension",
)

# Allow requests from CEP panel (running inside CEF browser with localhost or local origins)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

job_manager = JobManager()


@app.get("/")
def get_root() -> dict[str, Any]:
    return {
        "status": "ok",
        "message": "StyleCut Engine is running",
        "version": "0.1.0",
        "endpoints": ["/health", "/jobs/captions", "/jobs/{id}/progress", "/jobs/{id}/result"]
    }


@app.get("/health", response_model=HealthResponse)
def get_health() -> HealthResponse:
    """Health check endpoint used by the Premiere CEP panel to verify connection."""
    cuda_avail = False
    device = "cpu"
    try:
        import torch
        cuda_avail = torch.cuda.is_available()
        device = "cuda" if cuda_avail else "cpu"
    except Exception:
        pass

    ffmpeg_found = shutil.which("ffmpeg") is not None
    gemini_key = os.environ.get("GEMINI_API_KEY")
    gemini_found = bool(gemini_key and len(gemini_key) > 5)

    return HealthResponse(
        status="ok",
        version="0.1.0",
        engine="StyleCut Engine",
        cuda_available=cuda_avail,
        device=device,
        ffmpeg_available=ffmpeg_found,
        gemini_configured=gemini_found,
    )


@app.post("/jobs/captions", response_model=JobCreationResponse)
def create_caption_job(req: CaptionJobRequest) -> JobCreationResponse:
    """Start an asynchronous caption job for a media file."""
    path = Path(req.media_path)
    if not path.exists():
        raise HTTPException(
            status_code=400,
            detail=f"Media file not found on disk: {req.media_path}",
        )

    job_id = job_manager.create_caption_job(req.model_dump())
    return JobCreationResponse(
        job_id=job_id,
        status=job_manager.get_progress(job_id).status,
        message="Caption generation started.",
    )


@app.get("/jobs/{job_id}/progress", response_model=JobProgressResponse)
def get_job_progress(job_id: str) -> JobProgressResponse:
    """Poll progress (0-100%) and current execution step."""
    progress = job_manager.get_progress(job_id)
    if not progress:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    return progress


@app.get("/jobs/{job_id}/result", response_model=CaptionJobResultResponse)
def get_job_result(job_id: str) -> CaptionJobResultResponse:
    """Fetch completed result file paths (English SRT, Hinglish SRT, ASS)."""
    result = job_manager.get_result(job_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    return result


@app.get("/templates")
def list_templates() -> list[dict[str, Any]]:
    """List available Remotion caption templates with style details."""
    return [
        {"id": "hook_gold", "name": "Viral Hook (Golden Focus)", "description": "Montserrat bold with radiant golden keyword pop", "thumbnail": ""},
        {"id": "clean_one_line", "name": "Clean One Line", "description": "Crisp single-line minimalist reel caption", "thumbnail": ""},
        {"id": "boxed_highlight", "name": "Boxed Highlight", "description": "High contrast rounded pill highlight box", "thumbnail": ""},
        {"id": "bold_pop", "name": "Bold Pop", "description": "Dynamic spring bounce with punchy typography", "thumbnail": ""},
        {"id": "glow", "name": "Neon Glow", "description": "Electric cyan and violet ambient glow", "thumbnail": ""},
        {"id": "minimal_shadow", "name": "Minimal Shadow", "description": "Subtle, elegant cinematic drop shadow", "thumbnail": ""},
    ]


@app.get("/captions/lines")
def get_caption_lines(job_id: Optional[str] = None) -> dict[str, Any]:
    """Retrieve editable caption lines from the specified or latest job."""
    manifest_file: Optional[Path] = None

    if job_id:
        cand = Path.home() / ".stylecut" / "jobs" / job_id / "remotion_clips" / "captions_manifest.json"
        if cand.exists():
            manifest_file = cand

    if not manifest_file or not manifest_file.exists():
        jobs_dir = Path.home() / ".stylecut" / "jobs"
        if jobs_dir.exists():
            subdirs = sorted(jobs_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
            for sd in subdirs:
                mf = sd / "remotion_clips" / "captions_manifest.json"
                if mf.exists():
                    manifest_file = mf
                    break

    if not manifest_file or not manifest_file.exists():
        return {"ok": False, "lines": [], "message": "No caption jobs found."}

    spec_file = manifest_file.parent / "captions.json"
    if not spec_file.exists():
        return {"ok": False, "lines": [], "message": "captions.json not found."}

    try:
        spec_data = json.loads(spec_file.read_text(encoding="utf-8"))
        return {
            "ok": True,
            "manifest_path": str(manifest_file.resolve()),
            "template_id": spec_data.get("templateId", "hook_gold"),
            "canvas": spec_data.get("canvas", {}),
            "lines": spec_data.get("lines", []),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read lines: {e}")


@app.post("/captions/edit")
def edit_caption_line(req: CaptionEditRequest) -> dict[str, Any]:
    """Edit a single caption line (text, highlighted keyword, timing, style) and re-render only that clip in real-time."""
    manifest_file: Optional[Path] = None

    if req.manifest_path and Path(req.manifest_path).exists():
        manifest_file = Path(req.manifest_path)
    elif req.job_id:
        cand = Path.home() / ".stylecut" / "jobs" / req.job_id / "remotion_clips" / "captions_manifest.json"
        if cand.exists():
            manifest_file = cand

    if not manifest_file or not manifest_file.exists():
        jobs_dir = Path.home() / ".stylecut" / "jobs"
        if jobs_dir.exists():
            subdirs = sorted(jobs_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
            for sd in subdirs:
                mf = sd / "remotion_clips" / "captions_manifest.json"
                if mf.exists():
                    manifest_file = mf
                    break

    if not manifest_file or not manifest_file.exists():
        raise HTTPException(status_code=404, detail="captions_manifest.json not found.")

    output_dir = manifest_file.parent
    spec_file = output_dir / "captions.json"
    if not spec_file.exists():
        raise HTTPException(status_code=404, detail=f"captions.json not found in {output_dir}")

    try:
        spec_data = json.loads(spec_file.read_text(encoding="utf-8"))
        manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read caption spec: {e}")

    lines = spec_data.get("lines", [])
    target_line: Optional[dict[str, Any]] = None
    for line in lines:
        if line.get("id") == req.line_id:
            target_line = line
            break

    if not target_line:
        raise HTTPException(status_code=404, detail=f"Line with ID '{req.line_id}' not found.")

    # 1. Update text if requested
    if req.text is not None:
        new_text = req.text.strip()
        target_line["text"] = new_text
        tokens = [t for t in new_text.split() if t]
        existing_words = target_line.get("words", [])

        # If words list is missing or doesn't match new tokens, synthesize word timings
        if req.words is None and tokens:
            if not existing_words or [w.get("word") for w in existing_words] != tokens:
                start_t = float(target_line.get("start", 0.0))
                dur = float(target_line.get("duration", 1.0))
                step = dur / max(len(tokens), 1)
                new_words = []
                for i, tok in enumerate(tokens):
                    new_words.append({
                        "word": tok,
                        "start": round(start_t + i * step, 3),
                        "end": round(start_t + (i + 1) * step, 3),
                        "highlight": False,
                    })
                target_line["words"] = new_words

                # Ensure a valid keyword candidate from new tokens
                current_kw = target_line.get("keyword", "")
                if req.keyword is None and current_kw.lower() not in [t.lower() for t in tokens]:
                    target_line["keyword"] = max(tokens, key=len)
                    target_line["emphasis"] = target_line["keyword"]

    # 2. Update keyword / highlight customization
    effective_kw = req.keyword if req.keyword is not None else target_line.get("keyword")
    if effective_kw:
        clean_kw = effective_kw.strip()
        target_line["keyword"] = clean_kw
        target_line["emphasis"] = clean_kw

        words = target_line.get("words", [])
        clean_target = clean_kw.lower().strip(".,!?:;\"'()[]{}")
        kw_idx = -1
        for idx, w in enumerate(words):
            w_clean = w.get("word", "").lower().strip(".,!?:;\"'()[]{}")
            if w_clean == clean_target:
                w["highlight"] = True
                kw_idx = idx
            else:
                w["highlight"] = False

        if kw_idx != -1 and words:
            target_line["lead"] = " ".join(w["word"] for w in words[:kw_idx])
            target_line["keyword"] = words[kw_idx]["word"]
            target_line["tail"] = " ".join(w["word"] for w in words[kw_idx + 1:])
        else:
            # Custom keyword
            target_line["lead"] = ""
            target_line["keyword"] = clean_kw
            target_line["tail"] = ""

    if req.words is not None:
        target_line["words"] = req.words

    if req.start is not None:
        target_line["start"] = round(float(req.start), 3)

    if req.duration is not None:
        target_line["duration"] = round(float(req.duration), 3)
        target_line["end"] = round(target_line["start"] + target_line["duration"], 3)

    # 3. Merge style overrides
    style_overrides = {**spec_data.get("styleOverrides", {})}
    if req.style_overrides:
        style_overrides.update(req.style_overrides)

    canvas_cfg = spec_data.get("canvas", {"width": 1080, "height": 1920, "fps": 30})
    template_id = spec_data.get("templateId", "hook_gold")
    repo_root = Path(__file__).resolve().parent.parent.parent
    engine_dir = repo_root / "captions_engine"
    cache_dir = Path.home() / ".stylecut" / "cache" / "remotion"

    # 4. Re-render this single line clip
    try:
        clip_res = render_single_line(
            engine_dir=engine_dir,
            template_id=template_id,
            canvas=canvas_cfg,
            style_overrides=style_overrides,
            line=target_line,
            output_dir=output_dir,
            cache_dir=cache_dir,
            entry_point=".remotion_bundle",
        )
    except Exception as re_err:
        logger.exception(f"Failed to re-render line {req.line_id}: {re_err}")
        raise HTTPException(status_code=500, detail=f"Failed to re-render clip: {re_err}")

    # 5. Update captions.json and captions_manifest.json on disk
    spec_file.write_text(json.dumps(spec_data, ensure_ascii=False, indent=2), encoding="utf-8")

    clips = manifest_data.get("clips", [])
    for idx, c in enumerate(clips):
        if c.get("lineId") == req.line_id:
            clips[idx] = clip_res
            break
    else:
        clips.append(clip_res)
        clips.sort(key=lambda x: x.get("start", 0))

    manifest_data["clips"] = clips
    manifest_file.write_text(json.dumps(manifest_data, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info(f"Re-rendered line {req.line_id} successfully: {clip_res['path']}")
    return {
        "ok": True,
        "line_id": req.line_id,
        "clip_path": clip_res["path"],
        "start": clip_res["start"],
        "end": clip_res["end"],
        "duration": clip_res["duration"],
        "track": clip_res.get("track", 2),
        "line": target_line,
        "cached": clip_res.get("cached", False),
    }
