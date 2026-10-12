"""
LLM provider abstraction layer.

To add a new provider:
  1. Subclass ``LLMProvider`` and implement ``_generate(prompt) -> str``.
  2. Register it in ``get_provider()`` below.
  3. Set ``provider: <name>`` in config.yaml.

All calls are disk-cached (SHA-256 of prompt → JSON file) so identical
prompts never hit the API twice — even across restarts.
"""
from __future__ import annotations

import hashlib
import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class LLMProvider(ABC):
    """Abstract base class for StyleCut LLM backends."""

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ── cache helpers ──────────────────────────────────────────────

    def _cache_key(self, prompt: str) -> str:
        return hashlib.sha256(prompt.encode("utf-8")).hexdigest()

    def _load_cache(self, key: str) -> Optional[str]:
        path = self.cache_dir / f"{key}.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))["response"]
            except (json.JSONDecodeError, KeyError):
                pass  # corrupt cache → regenerate
        return None

    def _save_cache(self, key: str, response: str) -> None:
        path = self.cache_dir / f"{key}.json"
        path.write_text(
            json.dumps({"response": response}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ── public API ─────────────────────────────────────────────────

    def generate(self, prompt: str, *, use_cache: bool = True) -> str:
        """Generate a response, serving from disk cache when available.

        Args:
            prompt: The full prompt string.
            use_cache: If False, skip cache lookup (still writes result to cache).

        Returns:
            The LLM response as a plain string.
        """
        key = self._cache_key(prompt)
        if use_cache:
            cached = self._load_cache(key)
            if cached is not None:
                logger.debug(f"[llm] Cache hit  {key[:8]}…")
                return cached
        response = self._generate(prompt)
        self._save_cache(key, response)
        return response

    @abstractmethod
    def _generate(self, prompt: str) -> str:
        """Call the underlying API.  Implement in subclasses."""
        ...


# ── factory ───────────────────────────────────────────────────────

def get_provider(cfg: dict, cache_dir: Path) -> LLMProvider:
    """Instantiate the LLM provider defined in *cfg*.

    Args:
        cfg:       The ``llm:`` section of config.yaml.
        cache_dir: Directory for caching LLM responses.

    Returns:
        A ready-to-use ``LLMProvider`` instance.

    Raises:
        ValueError: If the provider name is unknown.
    """
    provider = cfg.get("provider", "gemini").lower()
    model = cfg.get("model", "gemini-2.0-flash")
    max_retries = int(cfg.get("max_retries", 3))
    backoff_sec = float(cfg.get("backoff_sec", 5.0))

    if provider == "gemini":
        from .gemini import GeminiProvider
        return GeminiProvider(
            cache_dir=cache_dir,
            model=model,
            max_retries=max_retries,
            backoff_sec=backoff_sec,
        )
    if provider == "ollama":
        from .ollama import OllamaProvider
        return OllamaProvider(cache_dir=cache_dir, model=model)

    raise ValueError(
        f"Unknown LLM provider: {provider!r}. "
        "Valid options: gemini, ollama"
    )
