"""Root conftest — make src importable and provide shared fixtures."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import shutil
import os

# Ensure src/ is importable from tests/ without installing the package
sys.path.insert(0, str(Path(__file__).parent))

# Ensure standard ffmpeg path is on PATH if not already present
if not shutil.which("ffmpeg"):
    for candidate in [r"C:\ffmpeg\bin"]:
        if os.path.exists(os.path.join(candidate, "ffmpeg.exe")):
            os.environ["PATH"] = candidate + os.pathsep + os.environ.get("PATH", "")
            break


# ── session-scoped synthetic clip ────────────────────────────────────────────

@pytest.fixture(scope="session")
def sample_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """5-second 640×480 MP4 with a 440 Hz audio tone.

    Session-scoped — created once, reused by all tests.
    Skips automatically if ffmpeg is not on PATH.
    """
    out: Path = tmp_path_factory.mktemp("clips") / "test_clip.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "color=c=blue:size=640x480:duration=5:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
        "-c:v", "libx264", "-crf", "28", "-preset", "ultrafast",
        "-c:a", "aac", "-b:a", "64k",
        str(out),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True)
    except FileNotFoundError:
        pytest.skip("ffmpeg not on PATH — skipping video-dependent tests")
    if result.returncode != 0:
        pytest.skip("ffmpeg failed to create test clip — skipping video-dependent tests")
    return out


# ── minimal fake transcript ──────────────────────────────────────────────────

@pytest.fixture
def sample_transcript() -> dict:
    """A minimal word-level transcript (no real Whisper needed)."""
    words = [
        {"word": " Hello", "start": 0.0,  "end": 0.5,  "probability": 0.99},
        {"word": " world", "start": 0.5,  "end": 0.9,  "probability": 0.98},
        {"word": " this",  "start": 0.9,  "end": 1.1,  "probability": 0.97},
        {"word": " is",    "start": 1.1,  "end": 1.3,  "probability": 0.99},
        {"word": " a",     "start": 1.3,  "end": 1.4,  "probability": 0.95},
        {"word": " test",  "start": 1.4,  "end": 2.5,  "probability": 0.98},
    ]
    return {
        "clip": "test.mp4",
        "language": "en",
        "language_probability": 0.99,
        "duration_sec": 5.0,
        "segments": [
            {
                "start": 0.0,
                "end": 2.5,
                "text": "Hello world this is a test",
                "words": words,
            }
        ],
        "words": words,
    }
