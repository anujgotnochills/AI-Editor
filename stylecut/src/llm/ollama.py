"""
Ollama local-LLM backend (stub — activated in Phase 8).

Prerequisites:
  1. Install Ollama: https://ollama.com/
  2. Pull a model: ``ollama pull llama3.2``
  3. Start the server: ``ollama serve``
  4. Set ``provider: ollama`` and ``model: llama3.2`` in config.yaml.
"""
from __future__ import annotations

import logging
from pathlib import Path

from .base import LLMProvider

logger = logging.getLogger(__name__)


class OllamaProvider(LLMProvider):
    """Ollama local-inference backend."""

    def __init__(
        self,
        cache_dir: Path,
        model: str = "llama3.2",
        base_url: str = "http://localhost:11434",
    ) -> None:
        super().__init__(cache_dir)
        self.model = model
        self.base_url = base_url.rstrip("/")
        logger.debug(f"[ollama] model={model}  base_url={base_url}")

    def _generate(self, prompt: str) -> str:
        try:
            import requests
        except ImportError:
            raise ImportError("requests not installed.  Run: pip install requests")

        url = f"{self.base_url}/api/generate"
        payload = {"model": self.model, "prompt": prompt, "stream": False}
        logger.info(f"[ollama] POST {url} model={self.model}")
        resp = requests.post(url, json=payload, timeout=120)
        resp.raise_for_status()
        return resp.json()["response"]
