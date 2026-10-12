"""
Transcription module using faster-whisper.

Features:
- Word-level timestamps (required for caption highlight and cut detection)
- GPU (CUDA / int8_float16) with automatic CPU fallback
- Per-clip disk caching keyed by file metadata hash
- VRAM freed immediately after each clip so the LLM can follow safely
"""
from __future__ import annotations

import json
import hashlib
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# PyAV >=14 compatibility shim: faster-whisper passes metadata_errors="ignore"
# which newer PyAV releases removed from av.open().
try:
    import av
    _orig_av_open = av.open

    def _safe_av_open(*args: Any, **kwargs: Any) -> Any:
        kwargs.pop("metadata_errors", None)
        return _orig_av_open(*args, **kwargs)

    av.open = _safe_av_open
except ImportError:
    pass



# ─────────────────────────── helpers ────────────────────────────────────────

def _clip_key(path: Path) -> str:
    """Cache key: MD5(resolved_path + size + mtime_ns)."""
    st = path.stat()
    raw = f"{path.resolve()}:{st.st_size}:{st.st_mtime_ns}"
    return hashlib.md5(raw.encode()).hexdigest()


def _load_whisper(model: str, device: str, compute_type: str, cpu_threads: int = 8):
    """Import faster-whisper and return a WhisperModel instance with multithreading."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise ImportError(
            "faster-whisper not installed.  Run: pip install faster-whisper"
        )
    if device == "cpu":
        return WhisperModel(model, device="cpu", compute_type=compute_type, cpu_threads=cpu_threads)
    return WhisperModel(model, device=device, compute_type=compute_type)


def _free_vram() -> None:
    """Release cached CUDA tensors so the next process (LLM) can use the VRAM."""
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


# ─────────────────────────── public API ─────────────────────────────────────

def transcribe_clip(
    path: Path,
    cache_dir: Path,
    *,
    model: str = "small",
    device: str = "cpu",
    compute_type: str = "int8",
    fallback_model: str = "base",
    fallback_device: str = "cpu",
    fallback_compute_type: str = "int8",
    language: Optional[str] = None,
    initial_prompt: Optional[str] = None,
    progress_callback: Optional[Any] = None,
) -> dict[str, Any]:
    """Transcribe a single clip and return a word-level transcript dict.

    The result is cached as ``cache_dir/<hash>.json``.  Unchanged clips are
    never re-transcribed.  VRAM is freed before the function returns.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    key = _clip_key(path)
    cache_file = cache_dir / f"{key}.json"

    if cache_file.exists():
        logger.info(f"[transcribe] Cache hit:  {path.name}")
        return json.loads(cache_file.read_text(encoding="utf-8"))

    def _execute(m: str, dev: str, comp: str) -> dict[str, Any]:
        logger.info(f"[transcribe] Loading {m} on {dev}…")
        wmodel = _load_whisper(m, dev, comp)
        try:
            logger.info(f"[transcribe] Transcribing {path.name} [{dev}]…")
            segments_gen, info = wmodel.transcribe(
                str(path),
                word_timestamps=True,
                language=language,
                initial_prompt=initial_prompt,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 300},
            )

            total_dur = getattr(info, "duration", 0.0) or 0.0

            segments: list[dict] = []
            words_flat: list[dict] = []

            # Materialise generator with real-time streaming progress updates
            for seg in segments_gen:
                if progress_callback and total_dur > 0:
                    try:
                        progress_callback(seg.end, total_dur)
                    except Exception:
                        pass
                word_list: list[dict] = []
                for w in seg.words or []:
                    entry: dict[str, Any] = {
                        "word": w.word,
                        "start": round(w.start, 3),
                        "end": round(w.end, 3),
                        "probability": round(w.probability, 4),
                    }
                    word_list.append(entry)
                    words_flat.append(entry)

                segments.append({
                    "start": round(seg.start, 3),
                    "end": round(seg.end, 3),
                    "text": seg.text.strip(),
                    "words": word_list,
                })

            return {
                "clip": str(path),
                "language": info.language,
                "language_probability": round(info.language_probability, 4),
                "duration_sec": round(info.duration, 3),
                "segments": segments,
                "words": words_flat,
            }
        finally:
            del wmodel
            _free_vram()

    try:
        result = _execute(model, device, compute_type)
    except Exception as exc:
        if device != fallback_device or model != fallback_model:
            logger.warning(
                f"[transcribe] {model}/{device} failed ({type(exc).__name__}: {exc}). "
                f"Falling back to {fallback_model}/{fallback_device}."
            )
            result = _execute(fallback_model, fallback_device, fallback_compute_type)
        else:
            raise

    cache_file.write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info(
        f"[transcribe] Done: {len(result['segments'])} segments, "
        f"{len(result['words'])} words, lang={result['language']} "
        f"({result['language_probability']:.0%})"
    )
    return result


def transcribe_project(
    clips: list[dict[str, Any]],
    cache_dir: Path,
    cfg: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Transcribe all clips in a project.

    Args:
        clips:     List of clip metadata dicts from ``ingest()``.
        cache_dir: Per-project transcript cache directory.
        cfg:       The ``whisper:`` section of config.yaml.

    Returns:
        ``{filename: transcript_dict}`` for every clip that has audio.
        Clips flagged NO_AUDIO are silently skipped.
    """
    results: dict[str, dict] = {}
    for clip in clips:
        if not clip.get("has_audio"):
            logger.info(f"[transcribe] Skipping {clip['filename']} — no audio track")
            continue
        path = Path(clip["path"])
        transcript = transcribe_clip(
            path=path,
            cache_dir=cache_dir,
            model=cfg.get("model", "distil-large-v3"),
            device=cfg.get("device", "cuda"),
            compute_type=cfg.get("compute_type", "int8_float16"),
            fallback_model=cfg.get("fallback_model", "medium"),
            fallback_device=cfg.get("fallback_device", "cpu"),
            fallback_compute_type=cfg.get("fallback_compute_type", "int8"),
            language=cfg.get("language"),
            initial_prompt=cfg.get("initial_prompt"),
        )
        results[clip["filename"]] = transcript
    return results
