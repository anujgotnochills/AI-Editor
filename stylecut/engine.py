"""StyleCut Engine Server — Entry point.

Starts the FastAPI server on 127.0.0.1:7820 to service the Premiere Pro CEP panel.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Ensure src/ is importable
_REPO_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(_REPO_ROOT))

import uvicorn
from src.server.app import app
from src.utils import setup_logging, load_env

# Ensure environment variables are available
load_env()


def main() -> None:
    parser = argparse.ArgumentParser(description="StyleCut Local Engine Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host interface (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=7820, help="Port (default: 7820)")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    # User folder for logs as required by specifications
    log_dir = Path.home() / ".stylecut" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "engine.log"

    level = logging.DEBUG if args.debug else logging.INFO
    setup_logging(log_file=log_file, level=level)

    logger = logging.getLogger("stylecut.engine")
    logger.info(f"Starting StyleCut Engine on http://{args.host}:{args.port}")
    logger.info(f"Engine log file: {log_file}")

    uvicorn.run("src.server.app:app", host=args.host, port=args.port, log_level="info", reload=True)


if __name__ == "__main__":
    main()
