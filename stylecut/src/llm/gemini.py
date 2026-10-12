"""
Google Gemini backend for the StyleCut LLM layer.

Uses the google-generativeai SDK with exponential-backoff retry.
Reads GEMINI_API_KEY from the environment (set via .env or system env).
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from .base import LLMProvider

logger = logging.getLogger(__name__)


class GeminiProvider(LLMProvider):
    """Google Gemini via the ``google-generativeai`` SDK."""

    def __init__(
        self,
        cache_dir: Path,
        model: str = "gemini-flash-latest",
        max_retries: int = 3,
        backoff_sec: float = 5.0,
    ) -> None:
        super().__init__(cache_dir)

        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "GEMINI_API_KEY environment variable is not set.\n"
                "  Get a free key at: https://aistudio.google.com/apikey\n"
                "  Then add it to your .env file:  GEMINI_API_KEY=<your_key>"
            )

        try:
            import google.generativeai as genai
        except ImportError:
            raise ImportError(
                "google-generativeai not installed.\n"
                "  Run: pip install google-generativeai"
            )

        genai.configure(api_key=api_key)
        self._client = genai.GenerativeModel(model)
        self._model_name = model
        self.max_retries = max_retries
        self.backoff_sec = backoff_sec
        logger.debug(f"[gemini] Initialized model={model}")

    def _generate(self, prompt: str) -> str:
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                response = self._client.generate_content(prompt)
                return response.text
            except Exception as exc:
                last_exc = exc
                wait = self.backoff_sec * (2 ** attempt)
                logger.warning(
                    f"[gemini] Attempt {attempt + 1}/{self.max_retries} failed: "
                    f"{type(exc).__name__}: {exc}.  Retrying in {wait:.0f}s…"
                )
                time.sleep(wait)

        raise RuntimeError(
            f"Gemini API ({self._model_name}) failed after "
            f"{self.max_retries} attempts."
        ) from last_exc
