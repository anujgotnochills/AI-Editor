"""Decision engine for Cut Engine v2.

Handles:
- Sentence and clause segmentation
- Configurable filler word detection with confidence gating
- Retake detection via fuzzy string matching (discards false starts, preserves best/last take)
- Gap shortening with keep_gap_ms (preserves breathing room between phrases)
- Dramatic pause preservation from LLM/transcript tags
"""
from __future__ import annotations

import logging
import re
import string
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from rapidfuzz import fuzz

from src.cut_engine.config import PacingConfig

logger = logging.getLogger(__name__)

_PUNCT_TABLE = str.maketrans("", "", string.punctuation + "।,॥?!'\"-–—()[]{}")


def clean_text(text: str) -> str:
    """Lowercase string stripped of punctuation."""
    return text.translate(_PUNCT_TABLE).strip().lower()


@dataclass
class WordToken:
    word: str
    start: float
    end: float
    probability: float = 0.90
    aligned: bool = False
    is_filler: bool = False
    filler_confidence: float = 0.0
    is_retake: bool = False
    is_dramatic_pause: bool = False


@dataclass
class ClauseSegment:
    words: list[WordToken]
    text: str
    start: float
    end: float
    is_retake: bool = False
    retake_of_index: Optional[int] = None
    has_dramatic_pause: bool = False


@dataclass
class KeepInterval:
    start: float
    end: float
    duration: float
    reason: str = "speech"
    confidence: float = 1.0
    words: list[dict[str, Any]] = field(default_factory=list)
    flagged: bool = False
    notes: str = ""


# ─────────────────────────── Clause Segmentation ─────────────────────────────

def segment_into_clauses(words: list[WordToken], max_clause_words: int = 14) -> list[ClauseSegment]:
    """Segment word tokens into clauses based on punctuation and inter-word pauses."""
    if not words:
        return []

    clauses: list[ClauseSegment] = []
    current_tokens: list[WordToken] = []

    terminal_punct = re.compile(r"[.!?।,॥;:]$")

    for i, w in enumerate(words):
        current_tokens.append(w)
        is_terminal = bool(terminal_punct.search(w.word))
        
        pause_to_next = 0.0
        if i < len(words) - 1:
            pause_to_next = words[i + 1].start - w.end

        should_split = (
            is_terminal
            or (pause_to_next >= 0.35)
            or (len(current_tokens) >= max_clause_words)
        )

        if should_split and current_tokens:
            clause_text = " ".join(t.word for t in current_tokens).strip()
            clauses.append(ClauseSegment(
                words=list(current_tokens),
                text=clause_text,
                start=current_tokens[0].start,
                end=current_tokens[-1].end,
            ))
            current_tokens = []

    if current_tokens:
        clause_text = " ".join(t.word for t in current_tokens).strip()
        clauses.append(ClauseSegment(
            words=list(current_tokens),
            text=clause_text,
            start=current_tokens[0].start,
            end=current_tokens[-1].end,
        ))

    return clauses


# ─────────────────────────── Filler Word Detection ───────────────────────────

def detect_fillers(
    words: list[WordToken],
    filler_vocab: Iterable[str],
    confidence_threshold: float = 0.70,
) -> list[WordToken]:
    """Flag filler words in tokens with confidence scoring."""
    filler_set = {clean_text(f) for f in filler_vocab if f}

    for w in words:
        cleaned = clean_text(w.word)
        if cleaned in filler_set:
            # Short isolated filler words have high filler probability
            duration = max(0.01, w.end - w.start)
            # Fillers that are unusually long (> 1.2s) might be real words or elongated vowels
            conf = w.probability
            if duration > 1.2:
                conf *= 0.6  # lower confidence if unusually long

            w.is_filler = True
            w.filler_confidence = round(conf, 2)
        else:
            w.is_filler = False
            w.filler_confidence = 0.0

    return words


# ─────────────────────────── Retake Detection ────────────────────────────────

def detect_retakes(
    clauses: list[ClauseSegment],
    similarity_threshold: float = 0.72,
) -> list[ClauseSegment]:
    """Detect repeated takes or false starts between adjacent or nearby clauses.

    When clause A and clause B share high fuzzy string similarity, the earlier clause A
    is flagged as a discarded retake (false start), and the later clause B is preserved.
    """
    n = len(clauses)
    for i in range(n - 1):
        if clauses[i].is_retake:
            continue

        text_a = clean_text(clauses[i].text)
        if len(text_a.split()) < 2:
            continue

        # Look at next 1 or 2 clauses for a restart
        for j in range(i + 1, min(i + 3, n)):
            text_b = clean_text(clauses[j].text)
            if len(text_b.split()) < 2:
                continue

            ratio = fuzz.ratio(text_a, text_b) / 100.0
            token_sort = fuzz.token_sort_ratio(text_a, text_b) / 100.0
            similarity = max(ratio, token_sort)

            # Prefix matching (speaker stopped mid-sentence and restarted)
            tokens_a = text_a.split()
            tokens_b = text_b.split()
            prefix_match = False
            if len(tokens_a) >= 2 and len(tokens_b) >= 2:
                common_prefix = 0
                for ta, tb in zip(tokens_a, tokens_b):
                    if ta == tb:
                        common_prefix += 1
                    else:
                        break
                if common_prefix >= 2 and (common_prefix / len(tokens_a)) >= 0.5:
                    prefix_match = True

            if similarity >= similarity_threshold or prefix_match:
                logger.info(
                    f"[decision] Retake detected (sim={similarity:.2f}):\n"
                    f"   Take 1 (discarded): \"{clauses[i].text}\"\n"
                    f"   Take 2 (kept):      \"{clauses[j].text}\""
                )
                clauses[i].is_retake = True
                clauses[i].retake_of_index = j
                for t in clauses[i].words:
                    t.is_retake = True
                break

    return clauses


# ─────────────────────────── Dramatic Pause Detection ────────────────────────

def check_dramatic_pauses(
    raw_transcript: dict[str, Any],
    words: list[WordToken],
) -> list[WordToken]:
    """Flag dramatic pauses if marked by [pause], [dramatic_pause], or LLM tags."""
    text_content = raw_transcript.get("text", "")
    has_pause_tag = bool(re.search(r"\[(dramatic_)?pause\]|\(pause\)", text_content, re.IGNORECASE))

    if not has_pause_tag:
        return words

    # Find words immediately preceding/succeeding the tag
    for w in words:
        if "[pause]" in w.word.lower() or "[dramatic_pause]" in w.word.lower():
            w.is_dramatic_pause = True

    return words


# ─────────────────────────── Decision Pipeline ───────────────────────────────

def decide_keep_intervals(
    word_dicts: list[dict[str, Any]],
    clip_duration: float,
    pacing: PacingConfig,
    raw_transcript: Optional[dict[str, Any]] = None,
    speech_regions: Optional[list[dict[str, float]]] = None,
) -> list[KeepInterval]:
    """Execute the full decision pipeline to produce clean, natural keep intervals.

    Pipeline:
    1. Parse WordTokens
    2. Filler word identification
    3. Clause segmentation & Retake detection
    4. Dramatic pause detection
    5. Gap shortening (leave keep_gap_ms between sentences instead of cutting to zero)
    6. Safety padding application
    """
    if clip_duration <= 0:
        return []

    if not word_dicts:
        # Fallback to speech regions from VAD if no words available
        if speech_regions:
            return [
                KeepInterval(
                    start=sr["start"],
                    end=sr["end"],
                    duration=sr["duration"],
                    reason="vad_speech",
                    confidence=0.85,
                )
                for sr in speech_regions
            ]
        return [KeepInterval(start=0.0, end=round(clip_duration, 3), duration=round(clip_duration, 3))]

    # 1. Convert to WordTokens
    tokens = [
        WordToken(
            word=w.get("word", "").strip(),
            start=float(w.get("start", 0.0)),
            end=float(w.get("end", 0.0)),
            probability=float(w.get("probability", 0.90)),
            aligned=bool(w.get("aligned", False)),
        )
        for w in word_dicts
        if w.get("word", "").strip()
    ]

    # 2. Filler detection
    if pacing.remove_fillers:
        tokens = detect_fillers(tokens, pacing.fillers, pacing.filler_confidence_threshold)

    # 3. Clause segmentation & Retake detection
    clauses = segment_into_clauses(tokens)
    if pacing.remove_retakes:
        clauses = detect_retakes(clauses, pacing.retake_similarity_threshold)

    # 4. Dramatic pauses
    if pacing.preserve_dramatic_pauses and raw_transcript:
        tokens = check_dramatic_pauses(raw_transcript, tokens)

    # 5. Filter kept tokens (drop retakes and confident fillers)
    kept_tokens: list[WordToken] = []
    discarded_fillers: list[WordToken] = []

    for t in tokens:
        if t.is_retake:
            continue
        if t.is_filler and t.filler_confidence >= pacing.filler_confidence_threshold:
            discarded_fillers.append(t)
            continue
        kept_tokens.append(t)

    if not kept_tokens:
        logger.warning("[decision] All words discarded by filters; keeping full clip as safety fallback.")
        return [KeepInterval(start=0.0, end=round(clip_duration, 3), duration=round(clip_duration, 3))]

    # 6. Build intervals with gap shortening and padding
    pad_before_sec = pacing.padding_before_ms / 1000.0
    pad_after_sec = pacing.padding_after_ms / 1000.0
    keep_gap_sec = pacing.keep_gap_ms / 1000.0
    min_silence_sec = pacing.min_silence_sec

    raw_intervals: list[dict[str, Any]] = []
    n_tokens = len(kept_tokens)

    for i in range(n_tokens):
        curr = kept_tokens[i]
        s = max(0.0, curr.start - pad_before_sec)
        e = min(clip_duration, curr.end + pad_after_sec)

        word_meta = {
            "word": curr.word,
            "start": curr.start,
            "end": curr.end,
            "probability": curr.probability,
        }

        # Check gap to next kept token
        if i < n_tokens - 1:
            nxt = kept_tokens[i + 1]
            gap = nxt.start - curr.end

            # Dramatic pause preservation
            if curr.is_dramatic_pause:
                raw_intervals.append({
                    "start": s,
                    "end": min(clip_duration, nxt.start),
                    "words": [word_meta],
                    "reason": "dramatic_pause",
                    "confidence": 0.95,
                })
                continue

            # If gap is smaller than min_silence_sec, bridge it completely
            if 0.0 <= gap < min_silence_sec:
                raw_intervals.append({
                    "start": s,
                    "end": min(clip_duration, nxt.start),
                    "words": [word_meta],
                    "reason": "speech",
                    "confidence": curr.probability,
                })
            else:
                # Gap >= min_silence_sec: Shorten gap to keep_gap_sec!
                # Split keep_gap_sec: half at tail of curr, half at lead of nxt
                half_gap = keep_gap_sec / 2.0
                extended_e = min(clip_duration, curr.end + max(pad_after_sec, half_gap))
                raw_intervals.append({
                    "start": s,
                    "end": extended_e,
                    "words": [word_meta],
                    "reason": "speech_silence_shortened",
                    "confidence": curr.probability,
                })
        else:
            raw_intervals.append({
                "start": s,
                "end": e,
                "words": [word_meta],
                "reason": "speech_tail",
                "confidence": curr.probability,
            })

    # 7. Merge overlapping or touching intervals
    merged: list[KeepInterval] = []
    for item in raw_intervals:
        s = round(item["start"], 3)
        e = round(item["end"], 3)
        words_list = item.get("words", [])
        reason = item.get("reason", "speech")
        conf = item.get("confidence", 0.95)

        if not merged:
            merged.append(KeepInterval(
                start=s,
                end=e,
                duration=round(e - s, 3),
                reason=reason,
                confidence=conf,
                words=list(words_list),
            ))
        else:
            prev = merged[-1]
            if s <= prev.end:  # overlap or contiguous
                prev.end = max(prev.end, e)
                prev.duration = round(prev.end - prev.start, 3)
                prev.words.extend(words_list)
                prev.confidence = min(prev.confidence, conf)
            else:
                merged.append(KeepInterval(
                    start=s,
                    end=e,
                    duration=round(e - s, 3),
                    reason=reason,
                    confidence=conf,
                    words=list(words_list),
                ))

    # 8. Filter micro-segments (< min_segment_duration_ms) unless contiguous
    min_dur_sec = pacing.min_segment_duration_ms / 1000.0
    final_intervals = [
        item for item in merged
        if item.duration >= min_dur_sec or len(item.words) > 0
    ]

    logger.info(
        f"[decision] Calculated {len(final_intervals)} keep intervals "
        f"({sum(k.duration for k in final_intervals):.1f}s retained / {clip_duration:.1f}s original, "
        f"{len(discarded_fillers)} fillers excised)."
    )

    return final_intervals
