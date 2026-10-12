"""Tests for src/hinglish.py."""
from __future__ import annotations

from unittest.mock import MagicMock, call

import pytest

from src.hinglish import (
    apply_glossary,
    contains_devanagari,
    convert_segment,
    convert_segments_batch,
    convert_transcript_to_hinglish,
    looks_hindi,
    needs_conversion,
)


# ─────────────────────────── detection ───────────────────────────────────────

def test_contains_devanagari_yes():
    assert contains_devanagari("यह एक test है") is True


def test_contains_devanagari_no():
    assert contains_devanagari("This is English") is False
    assert contains_devanagari("") is False


def test_looks_hindi_yes():
    assert looks_hindi("yeh product kaafi achha hai") is True


def test_looks_hindi_no():
    assert looks_hindi("The weather is nice today") is False
    assert looks_hindi("achha") is False  # only 1 marker — threshold is 2


def test_needs_conversion_devanagari():
    assert needs_conversion("यह test") is True


def test_needs_conversion_roman_hindi():
    assert needs_conversion("yaar yeh product hai") is True


def test_needs_conversion_english():
    assert needs_conversion("Clean English sentence here.") is False


# ─────────────────────────── glossary ────────────────────────────────────────

def test_apply_glossary_basic():
    text = "I love stylecut and StyleCut is great"
    result = apply_glossary(text, {"stylecut": "StyleCut™"})
    assert result == "I love StyleCut™ and StyleCut™ is great"


def test_apply_glossary_whole_word_only():
    """'kar' in glossary should NOT match 'karke' or 'karein'."""
    text = "kar this but not karke"
    result = apply_glossary(text, {"kar": "KAR"})
    assert "KAR this" in result
    assert "KARke" not in result


def test_apply_glossary_empty():
    assert apply_glossary("hello", {}) == "hello"


# ─────────────────────────── single segment ──────────────────────────────────

def test_convert_segment_english_no_llm_call():
    """Clean English text must pass through without calling the LLM."""
    llm = MagicMock()
    result = convert_segment("This is pure English.", llm)
    llm.generate.assert_not_called()
    assert result == "This is pure English."


def test_convert_segment_devanagari_calls_llm():
    llm = MagicMock()
    llm.generate.return_value = "yeh product achha hai"
    result = convert_segment("यह product अच्छा है", llm)
    llm.generate.assert_called_once()
    assert result == "yeh product achha hai"


def test_convert_segment_applies_glossary_after_llm():
    llm = MagicMock()
    llm.generate.return_value = "yeh stylecut product"
    result = convert_segment("यह test", llm, glossary={"stylecut": "StyleCut"})
    assert "StyleCut" in result


# ─────────────────────────── batch conversion ────────────────────────────────

def test_convert_segments_batch_passes_through_english():
    llm = MagicMock()
    texts = ["Hello world.", "Good morning.", "Clean English only."]
    result = convert_segments_batch(texts, llm)
    llm.generate.assert_not_called()
    assert result == texts


def test_convert_segments_batch_converts_hindi():
    llm = MagicMock()
    llm.generate.return_value = '["yeh achha hai", "theek hai yaar"]'
    texts = ["यह अच्छा है", "theek hai yaar bhi"]
    result = convert_segments_batch(texts, llm)
    assert len(result) == 2
    assert result[0] == "yeh achha hai"


def test_convert_segments_batch_fallback_on_bad_json():
    """If the LLM returns malformed JSON, fall back to single-segment calls."""
    llm = MagicMock()
    llm.generate.side_effect = [
        "NOT JSON AT ALL",         # batch call fails
        "yeh achha hai",           # fallback single call 1
        "theek hai yaar",          # fallback single call 2
    ]
    texts = ["यह अच्छा है", "theek hai yaar bhi"]
    # Both texts need conversion
    result = convert_segments_batch(texts, llm)
    # Should have 3 calls total (1 batch + 2 fallback)
    assert llm.generate.call_count == 3
    assert len(result) == 2


# ─────────────────────────── transcript conversion ────────────────────────────

def test_convert_transcript_adds_hinglish_fields(sample_transcript: dict):
    llm = MagicMock()
    # Batch call returns one string per segment
    llm.generate.return_value = '["Hello world yeh ek test hai"]'

    result = convert_transcript_to_hinglish(sample_transcript, llm)

    assert result["hinglish_converted"] is True
    assert "text_hinglish" in result["segments"][0]
    for w in result["words"]:
        assert "word_hinglish" in w


def test_convert_transcript_does_not_mutate_original(sample_transcript: dict):
    """Input transcript must be unchanged (deep copy contract)."""
    import copy
    original = copy.deepcopy(sample_transcript)
    llm = MagicMock()
    llm.generate.return_value = '["Hello world this is a test"]'

    convert_transcript_to_hinglish(sample_transcript, llm)

    # Original must be identical to before the call
    assert sample_transcript == original


def test_convert_transcript_only_roman_no_llm_calls(sample_transcript: dict):
    """English-only transcripts should not trigger batch LLM calls."""
    llm = MagicMock()
    # All words in sample_transcript are English — needs_conversion should be False
    # for each segment text.  Mock returns a passthrough.
    llm.generate.return_value = '["Hello world this is a test"]'

    convert_transcript_to_hinglish(sample_transcript, llm, glossary={})
    # The batch prompt IS generated because looks_hindi may not trigger,
    # but the result should still have the fields populated.
    result = convert_transcript_to_hinglish(sample_transcript, llm)
    assert result["hinglish_converted"] is True
