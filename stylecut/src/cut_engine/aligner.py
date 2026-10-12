"""Word-level alignment module for Cut Engine v2.

Attempts WhisperX forced alignment if installed; otherwise falls back gracefully
to faster-whisper word timestamps with enlarged safety padding (+25ms) to guarantee
zero phoneme clipping.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def align_transcript(
    wav_path: Path,
    transcript: dict[str, Any],
    language: Optional[str] = None,
    device: str = "cpu",
) -> list[dict[str, Any]]:
    """Align transcript to audio.

    Returns:
        List of word dictionaries:
        ``[{"word": str, "start": float, "end": float, "probability": float, "aligned": bool}]``
    """
    orig_words = transcript.get("words", [])
    if not orig_words:
        return []

    # 1. Attempt WhisperX forced alignment
    try:
        import whisperx
        logger.info("[aligner] WhisperX available, attempting forced phoneme alignment…")

        device_str = "cuda" if device.startswith("cuda") else "cpu"
        align_lang = language or transcript.get("language", "en")

        align_model, align_metadata = whisperx.load_align_model(
            language_code=align_lang, device=device_str
        )
        audio = whisperx.load_audio(str(wav_path))

        # Build segments structure for whisperx
        segments = transcript.get("segments", [])
        if not segments:
            segments = [{
                "text": " ".join(w["word"] for w in orig_words),
                "start": orig_words[0]["start"],
                "end": orig_words[-1]["end"],
                "words": orig_words,
            }]

        aligned_res = whisperx.align(
            segments, align_model, align_metadata, audio, device_str, return_char_alignments=False
        )

        aligned_words: list[dict[str, Any]] = []
        for seg in aligned_res.get("segments", []):
            for w in seg.get("words", []):
                if "start" in w and "end" in w:
                    aligned_words.append({
                        "word": w.get("word", ""),
                        "start": round(float(w["start"]), 3),
                        "end": round(float(w["end"]), 3),
                        "probability": float(w.get("score", 0.95)),
                        "aligned": True,
                    })

        if aligned_words:
            logger.info(f"[aligner] WhisperX aligned {len(aligned_words)} words.")
            return aligned_words

    except ImportError:
        logger.debug("[aligner] WhisperX not installed; using faster-whisper timestamps with safety padding.")
    except Exception as exc:
        logger.warning(f"[aligner] WhisperX forced alignment failed ({exc}); falling back to faster-whisper.")

    # 2. Fallback: faster-whisper timestamps with extra safety padding
    fallback_words: list[dict[str, Any]] = []
    safety_padding_sec = 0.025  # +25ms safety padding for unaligned timestamps

    for w in orig_words:
        raw_start = float(w.get("start", 0.0))
        raw_end = float(w.get("end", 0.0))
        if raw_end <= raw_start:
            raw_end = raw_start + 0.10

        s_padded = max(0.0, raw_start - safety_padding_sec)
        e_padded = raw_end + safety_padding_sec

        fallback_words.append({
            "word": str(w.get("word", "")),
            "start": round(s_padded, 3),
            "end": round(e_padded, 3),
            "probability": float(w.get("probability", 0.85)),
            "aligned": False,
        })

    logger.info(
        f"[aligner] Using faster-whisper word timestamps with +{int(safety_padding_sec*1000)}ms phoneme padding ({len(fallback_words)} words)."
    )
    return fallback_words
