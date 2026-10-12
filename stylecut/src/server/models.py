"""Pydantic schemas for the StyleCut Engine Server."""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.1.0"
    engine: str = "StyleCut Engine"
    cuda_available: bool = False
    device: str = "cpu"
    ffmpeg_available: bool = True
    gemini_configured: bool = False


class TimelineClip(BaseModel):
    name: Optional[str] = None
    media_path: Optional[str] = ""
    track: int = 0
    start: float = 0.0          # Start time on the timeline (seconds)
    end: float = 0.0            # End time on the timeline (seconds)
    duration: float = 0.0       # Duration on the timeline (seconds)
    in_point: float = 0.0       # Trim in-point in media (seconds)
    out_point: float = 0.0      # Trim out-point in media (seconds)


class CaptionJobRequest(BaseModel):
    media_path: str = Field(..., description="Absolute path to the video or audio file")
    sequence_duration: Optional[float] = Field(None, description="Total active sequence duration in seconds")
    timeline_clips: Optional[list[TimelineClip]] = Field(None, description="Timeline clips for frame-accurate sync")
    language: Optional[str] = Field(None, description="ISO language code (null for auto-detect)")
    hinglish: bool = Field(True, description="Whether to generate Hinglish Roman-script translation")
    model: Optional[str] = Field(None, description="Whisper model name (small, medium, distil-large-v3)")
    is_timeline_audio: bool = Field(False, description="True if media_path is already a pre-rendered timeline audio mix")
    words_per_line: int = Field(3, description="Words per line in generated subtitles")
    max_lines: int = Field(2, description="Maximum lines per subtitle display")
    template: Optional[str] = Field("hook", description="Captik-style template: hook, hormozi, mrbeast, ali, neon, skool, minimal")
    format: Optional[str] = Field("graphics", description="Target format: graphics or subtitles")
    sequence_width: int = Field(1080, description="Sequence width in pixels")
    sequence_height: int = Field(1920, description="Sequence height in pixels")


class JobCreationResponse(BaseModel):
    job_id: str
    status: JobStatus = JobStatus.QUEUED
    message: str = "Job created and queued."


class JobProgressResponse(BaseModel):
    job_id: str
    status: JobStatus
    pct: int = Field(0, description="Progress percentage 0-100")
    step: str = Field("Initializing", description="Current execution stage")
    error: Optional[str] = None


class CaptionJobResultResponse(BaseModel):
    job_id: str
    status: JobStatus
    srt_en: Optional[str] = Field(None, description="Path to English SRT")
    srt_hi: Optional[str] = Field(None, description="Path to Hinglish SRT")
    ass: Optional[str] = Field(None, description="Path to styled ASS captions")
    overlay_mov: Optional[str] = Field(None, description="Path to transparent ProRes 4444 overlay video")
    manifest_path: Optional[str] = Field(None, description="Path to captions_manifest.json")
    captions_spec: Optional[dict[str, Any]] = Field(None, description="Shared captions.json specification")
    clips: Optional[list[dict[str, Any]]] = Field(None, description="List of per-line rendered clips")
    duration_sec: float = 0.0
    segments_count: int = 0
    words_count: int = 0


class TemplateInfo(BaseModel):
    id: str
    name: str
    description: str
    thumbnail: Optional[str] = None


class CaptionEditRequest(BaseModel):
    manifest_path: Optional[str] = None
    job_id: Optional[str] = None
    line_id: str
    text: Optional[str] = None
    keyword: Optional[str] = None
    words: Optional[list[dict[str, Any]]] = None
    start: Optional[float] = None
    duration: Optional[float] = None
    style_overrides: Optional[dict[str, Any]] = None


class CaptionRestyleRequest(BaseModel):
    manifest_path: str
    template_id: str
    style_overrides: Optional[dict[str, Any]] = None

