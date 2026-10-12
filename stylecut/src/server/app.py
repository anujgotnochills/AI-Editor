"""FastAPI application for StyleCut local engine."""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from src.utils import load_env

# Ensure environment variables (.env) are loaded
load_env()

from src.server.jobs import JobManager
from src.server.models import (
    CaptionJobRequest,
    CaptionJobResultResponse,
    HealthResponse,
    JobCreationResponse,
    JobProgressResponse,
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
