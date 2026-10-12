"""
Shared utilities: logging setup, config loading, .env loading.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

import os
import shutil
import yaml


def ensure_ffmpeg_on_path() -> None:
    """Ensure ffmpeg/ffprobe directory is in PATH if not already accessible."""
    if not shutil.which("ffmpeg"):
        for candidate in [r"C:\ffmpeg\bin", r"C:\Program Files\ffmpeg\bin"]:
            if os.path.exists(os.path.join(candidate, "ffmpeg.exe")):
                os.environ["PATH"] = candidate + os.pathsep + os.environ.get("PATH", "")
                break


ensure_ffmpeg_on_path()


def setup_logging(
    log_file: Path | None = None,
    level: int = logging.INFO,
) -> None:
    """Configure root logger with console + optional file handler.

    Calling this multiple times is safe (force=True resets handlers).
    """
    fmt = "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(level=level, format=fmt, handlers=handlers, force=True)


def load_config(config_path: Path) -> dict[str, Any]:
    """Load and return a YAML config file as a plain dict."""
    with open(config_path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_env(project_root: Path | None = None) -> None:
    """Load .env into the environment.

    Search order:
      1. <project_root>/.env
      2. <repo_root>/.env  (same folder as main.py)
      3. Default python-dotenv search (cwd / home)
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return  # python-dotenv optional; env vars may already be set

    candidates: list[Path] = []
    if project_root:
        candidates.append(Path(project_root) / ".env")
    # repo root = two levels up from this file (src/utils.py → stylecut/)
    candidates.append(Path(__file__).parent.parent / ".env")

    for p in candidates:
        if p.exists():
            load_dotenv(p, override=False)
            return
    load_dotenv(override=False)  # fallback: default search
