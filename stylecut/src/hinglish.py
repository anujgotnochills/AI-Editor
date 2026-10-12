"""
Hinglish conversion module.

Workflow per transcript:
  1. Detect if each segment contains Devanagari or Roman-script Hindi markers.
  2. Segments that need conversion are batched (up to 8 per LLM call) to
     minimise API usage.  LLM responses are cached by the LLMProvider.
  3. Individual Devanagari words get their own ``word_hinglish`` field;
     already-Roman words are passed through with only glossary substitution.
  4. A per-client glossary (brand names, product terms) is applied after
     LLM conversion so brand terms are always spelled correctly.

Output fields added to each segment:
  - ``segment["text_hinglish"]``  — segment text in Roman-script Hinglish
  - ``word["word_hinglish"]``     — individual word in Hinglish
  - ``transcript["hinglish_converted"]`` = True
"""
from __future__ import annotations

import copy
import json
import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Unicode block for Devanagari script (Hindi, Marathi, Sanskrit…)
_DEVANAGARI = re.compile(r"[\u0900-\u097F]")

# Common Roman-script Hindi function words — used as a lightweight heuristic
_HINDI_MARKERS: frozenset[str] = frozenset({
    "hai", "ho", "hain", "kar", "ke", "ki", "ka", "mein", "se",
    "aur", "bhi", "nahi", "nahin", "yaar", "toh", "woh", "yeh",
    "kya", "ab", "jab", "tab", "agar", "lekin", "mat", "lo",
    "dekho", "suno", "achha", "theek", "bilkul", "matlab",
})

_SEGMENT_PROMPT = (
    "You are a Hinglish subtitle editor for Indian social-media videos.\n"
    "Convert the following text to natural Hinglish written in Roman script "
    "(English letters only — no Devanagari).\n"
    "Rules:\n"
    "  • Keep the exact same meaning, tone, and code-switching style.\n"
    "  • Do NOT translate fully to English.\n"
    "  • Preserve English brand names and product terms exactly as they appear.\n"
    "  • Return ONLY the converted text — no extra commentary.\n\n"
    "Input:\n{text}"
)

_BATCH_PROMPT = (
    "You are a Hinglish subtitle editor for Indian social-media videos.\n"
    "Convert each numbered text to natural Hinglish in Roman script.\n"
    "Rules:\n"
    "  • Same meaning, tone, and code-switching style.\n"
    "  • Do NOT translate fully to English.\n"
    "  • Preserve English brand names and product terms exactly.\n"
    "  • Return a JSON array of strings (one per input, same order, no extra keys).\n\n"
    "Input texts:\n{numbered}\n\n"
    "JSON array:"
)


# ─────────────────────────── detection helpers ───────────────────────────────

def contains_devanagari(text: str) -> bool:
    """Return True if *text* contains any Devanagari Unicode character."""
    return bool(_DEVANAGARI.search(text))


def looks_hindi(text: str) -> bool:
    """Heuristic: return True if ≥ 2 common Hindi function words are found."""
    tokens = set(re.findall(r"\b\w+\b", text.lower()))
    return len(tokens & _HINDI_MARKERS) >= 2


def needs_conversion(text: str) -> bool:
    """Return True if *text* should be sent through the Hinglish LLM pipeline."""
    return contains_devanagari(text) or looks_hindi(text)


# ─────────────────────────── glossary ───────────────────────────────────────

def apply_glossary(text: str, glossary: dict[str, str]) -> str:
    """Case-insensitive whole-word replacement of brand terms.

    Args:
        text:     Input string.
        glossary: Mapping of ``{wrong_spelling: correct_spelling}``.

    Returns:
        Text with all glossary terms replaced.
    """
    for term, replacement in glossary.items():
        pattern = re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)
        text = pattern.sub(replacement, text)
    return text


# ─────────────────────────── single segment ─────────────────────────────────

def convert_segment(
    text: str,
    llm,
    glossary: Optional[dict[str, str]] = None,
) -> str:
    """Convert a single text to Hinglish via LLM (or pass through unchanged).

    English-only text is returned as-is (no LLM call).
    The LLMProvider's built-in cache means repeated identical segments
    are free.
    """
    if not needs_conversion(text):
        result = text
    else:
        prompt = _SEGMENT_PROMPT.format(text=text)
        result = llm.generate(prompt).strip()

    if glossary:
        result = apply_glossary(result, glossary)
    return result


# ─────────────────────────── batch conversion ────────────────────────────────

def _parse_json_array(raw: str) -> list[str]:
    """Extract a JSON string array from an LLM response.

    Tolerates markdown code fences (```json … ```).
    """
    clean = raw.strip()
    clean = re.sub(r"^```(?:json)?\s*", "", clean)
    clean = re.sub(r"```\s*$", "", clean)
    parsed = json.loads(clean)
    if not isinstance(parsed, list):
        raise ValueError(f"Expected JSON array, got {type(parsed).__name__}")
    return [str(item) for item in parsed]


def convert_segments_batch(
    texts: list[str],
    llm,
    glossary: Optional[dict[str, str]] = None,
    batch_size: int = 8,
) -> list[str]:
    """Convert a list of segment texts to Hinglish efficiently.

    Texts that don't need conversion pass through unchanged.
    Those that do are grouped into batched LLM calls (up to *batch_size*
    per call).  Falls back to single-segment calls if the batch response
    can't be parsed.

    Args:
        texts:      List of segment text strings (any language).
        llm:        An ``LLMProvider`` instance.
        glossary:   Optional client brand glossary.
        batch_size: Max segments per LLM call.

    Returns:
        List of Hinglish strings, same length and order as *texts*.
    """
    results: list[str] = [""] * len(texts)
    to_convert: list[tuple[int, str]] = []

    # Pass-through anything that's already Roman / English
    for i, text in enumerate(texts):
        if needs_conversion(text):
            to_convert.append((i, text))
        else:
            results[i] = apply_glossary(text, glossary) if glossary else text

    # Batch the rest
    for b_start in range(0, len(to_convert), batch_size):
        batch = to_convert[b_start : b_start + batch_size]
        numbered = "\n".join(f"{j + 1}. {t}" for j, (_, t) in enumerate(batch))
        prompt = _BATCH_PROMPT.format(numbered=numbered)
        raw = llm.generate(prompt).strip()

        try:
            converted = _parse_json_array(raw)
            if len(converted) != len(batch):
                raise ValueError(
                    f"Response has {len(converted)} items for {len(batch)} inputs"
                )
        except Exception as exc:
            logger.warning(
                f"[hinglish] Batch parse failed ({exc}); "
                "falling back to per-segment conversion."
            )
            converted = [convert_segment(t, llm, None) for _, t in batch]

        for k, (orig_idx, _) in enumerate(batch):
            text = converted[k].strip()
            if glossary:
                text = apply_glossary(text, glossary)
            results[orig_idx] = text

    return results


# ─────────────────────────── transliteration helpers ─────────────────────────

_CONSONANTS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "ng",
    "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "ny",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n",
    "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "f", "ब": "b", "भ": "bh", "म": "m",
    "य": "y", "र": "r", "ल": "l", "व": "v",
    "श": "sh", "ष": "sh", "स": "s", "ह": "h",
    "क़": "q", "ख़": "kh", "ग़": "gh", "ज़": "z", "ड़": "r", "ढ़": "rh", "फ़": "f",
}

_INDEPENDENT_VOWELS = {
    "अ": "a", "आ": "aa", "इ": "i", "ई": "ee", "उ": "u", "ऊ": "oo",
    "ऋ": "ri", "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au",
}

_MATRAS = {
    "ा": "a", "ि": "i", "ी": "ee", "ु": "u", "ू": "oo",
    "ृ": "ri", "े": "e", "ै": "ai", "ो": "o", "ौ": "au",
    "्": "",
}

_SPECIAL = {
    "ं": "n", "ँ": "n", "ः": "h",
}

_COMMON_WORDS = {
    "मैं": "main", "हूँ": "hoon", "है": "hai", "हैं": "hain", "हो": "ho",
    "आप": "aap", "तुम": "tum", "तू": "tu", "हम": "hum", "वो": "woh", "यह": "yeh",
    "क्या": "kya", "क्यों": "kyun", "क्यूँ": "kyun", "कैसे": "kaise", "कैसा": "kaisa",
    "घर": "ghar", "पानी": "paani", "खाना": "khaana", "अच्छा": "achha", "ठीक": "theek",
    "नहीं": "nahi", "नही": "nahi", "बात": "baat", "करना": "karna", "कर": "kar",
    "रहा": "raha", "रही": "rahi", "रहे": "rahe", "था": "tha", "थी": "thi", "थे": "the",
    "बहुत": "bohot", "ज्यादा": "zyada", "कम": "kam", "आज": "aaj", "कल": "kal",
    "दो": "do", "एक": "ek", "तीन": "teen", "चार": "chaar", "पांच": "paanch", "छह": "chhe",
    "दिन": "din", "रात": "raat", "समय": "samay", "टाइम": "time", "दोस्त": "dost",
    "यार": "yaar", "भाई": "bhai", "सुनो": "suno", "देखो": "dekho", "बोलो": "bolo",
    "साथ": "saath", "पास": "paas", "दूर": "door", "ऊपर": "upar", "नीचे": "neeche",
    "अंदर": "andar", "बाहर": "baahar", "बाद": "baad", "पहले": "pehle", "फिर": "phir",
    "भी": "bhi", "तो": "toh", "पर": "par", "से": "se", "को": "ko", "ने": "ne", "का": "ka", "की": "ki", "के": "ke",
    "चे": "chhe", "बजे": "baje", "मेरा": "mera", "मेरी": "meri", "मेरे": "mere",
    "अजीब": "ajeeb", "अज़े": "ajeeb", "लगने": "lagne", "लगता": "lagta", "लगती": "lagti",
    "निकलती": "nikalti", "निकलता": "nikalta", "हल्दी": "healthy", "फ्रेज्ट": "fresh",
    "किड": "skin", "लीटर": "litre", "ध्यान": "dhyan", "दियान": "dhyan", "रखती": "rakhti",
    "पतानी": "pata nahi", "क्यू": "kyun",
}


def transliterate_word(w: str) -> str:
    """Fast, deterministic Devanagari to Roman Hinglish transliteration."""
    if not contains_devanagari(w):
        return w

    cleaned = re.sub(r"[^\w\u0900-\u097F]", "", w).strip()
    prefix = re.match(r"^[^\w\u0900-\u097F]+", w)
    suffix = re.search(r"[^\w\u0900-\u097F]+$", w)
    p_str = prefix.group(0) if prefix else ""
    s_str = suffix.group(0) if suffix else ""

    if cleaned in _COMMON_WORDS:
        return p_str + _COMMON_WORDS[cleaned] + s_str

    res = []
    i = 0
    chars = list(cleaned)
    n = len(chars)
    while i < n:
        ch = chars[i]
        if i + 1 < n and chars[i + 1] == '़':
            combo = ch + '़'
            if combo in _CONSONANTS:
                i += 2
                matra = chars[i] if i < n and chars[i] in _MATRAS else None
                if matra is not None:
                    res.append(_CONSONANTS[combo] + _MATRAS[matra])
                    i += 1
                else:
                    res.append(_CONSONANTS[combo] + ("" if i == n else "a"))
                continue

        if ch in _INDEPENDENT_VOWELS:
            res.append(_INDEPENDENT_VOWELS[ch])
            i += 1
        elif ch in _CONSONANTS:
            if i + 1 < n and chars[i + 1] in _MATRAS:
                res.append(_CONSONANTS[ch] + _MATRAS[chars[i + 1]])
                i += 2
            elif i + 1 < n and chars[i + 1] == "्":
                res.append(_CONSONANTS[ch])
                i += 2
            else:
                is_end = (i + 1 == n or not ('\u0900' <= chars[i + 1] <= '\u097F'))
                res.append(_CONSONANTS[ch] + ("" if is_end else "a"))
                i += 1
        elif ch in _SPECIAL:
            res.append(_SPECIAL[ch])
            i += 1
        elif ch in _MATRAS:
            res.append(_MATRAS[ch])
            i += 1
        else:
            res.append(ch)
            i += 1

    return p_str + "".join(res) + s_str


# ─────────────────────────── transcript conversion ───────────────────────────

def convert_transcript_to_hinglish(
    transcript: dict[str, Any],
    llm,
    glossary: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """Return a deep copy of *transcript* with Hinglish fields added.

    Adds:
    - ``segment["text_hinglish"]``  for every segment
    - ``word["word_hinglish"]``     for every word in segments and flat list
    - ``transcript["hinglish_converted"] = True``

    Devanagari words are converted to Roman script and client glossaries are
    applied.
    """
    result = copy.deepcopy(transcript)
    segments: list[dict] = result.get("segments", [])

    # ── 1. Batch-convert all segment texts ─────────────────────────
    texts = [seg["text"] for seg in segments]
    hinglish_texts = convert_segments_batch(texts, llm, glossary)

    for seg, h_text in zip(segments, hinglish_texts):
        seg["text_hinglish"] = h_text

        # ── 2. Word-level: convert Devanagari words ─────────────────
        for w in seg.get("words", []):
            raw = w["word"].strip()
            if contains_devanagari(raw):
                trans_word = transliterate_word(raw)
                w["word_hinglish"] = (
                    apply_glossary(trans_word, glossary) if glossary else trans_word
                )
            else:
                w["word_hinglish"] = (
                    apply_glossary(raw, glossary) if glossary else raw
                )

    # ── 3. Sync the flat word list ──────────────────────────────────
    for w in result.get("words", []):
        if "word_hinglish" not in w:
            raw = w["word"].strip()
            if contains_devanagari(raw):
                trans_word = transliterate_word(raw)
                w["word_hinglish"] = (
                    apply_glossary(trans_word, glossary) if glossary else trans_word
                )
            else:
                w["word_hinglish"] = (
                    apply_glossary(raw, glossary) if glossary else raw
                )

    result["hinglish_converted"] = True
    return result
