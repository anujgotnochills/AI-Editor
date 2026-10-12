# StyleCut — AI Video Editing Automation

> A local, free-to-run tool that turns raw footage + a script + a plain-language style instruction into a fully edited video: cuts, captions, colour, music, and SFX — automatically.

---

## System Requirements

| Requirement | Notes |
|---|---|
| Python 3.11+ | `python --version` |
| FFmpeg ≥ 6 (on PATH) | [Windows build](https://www.gyan.dev/ffmpeg/builds/) — get the "full" build for NVENC |
| NVIDIA GPU with CUDA ≥ 11.8 | Optional; falls back to CPU automatically |
| Gemini API key (free tier) | [Get one](https://aistudio.google.com/apikey) |

---

## Installation

```powershell
# 1. Clone / unzip the project
cd "D:\Personal Projects\ai editor\stylecut"

# 2. Create a virtual environment (recommended)
python -m venv .venv
.venv\Scripts\activate

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Set your API key
copy .env.example .env
# Open .env and paste your GEMINI_API_KEY

# 5. Verify FFmpeg
ffmpeg -version
```

---

## Quick Start (Phase 1 — Captions)

### Option A — Use your own footage

```
projects/
└── myreel/
    ├── footage/       ← drop your .mp4 / .mov clips here
    ├── script.txt     ← (optional for Phase 1)
    └── brief.txt      ← (optional for Phase 1)
```

```powershell
python main.py projects/myreel --captions-only
```

### Option B — Generate a synthetic sample

```powershell
# Requires: pip install gtts   (for speech audio; silent tone used as fallback)
python scripts/make_sample.py
python main.py projects/sample --captions-only
```

### Output (`projects/<name>/output/`)

| File | Description |
|---|---|
| `<clip>_en.srt` | English subtitles |
| `<clip>_hinglish.srt` | Roman-script Hinglish subtitles |
| `<clip>_word_highlight.ass` | Styled ASS with yellow word-highlight karaoke effect |
| `<clip>_captioned.mp4` | Video with burned-in captions |
| `captions_en.srt` | Convenience copy (single-clip projects) |
| `captions_hinglish.srt` | Convenience copy (single-clip projects) |
| `final_captioned.mp4` | Convenience copy (single-clip projects) |
| `log.txt` | Full run log |

---

## CLI Reference

```
python main.py <project_dir> [options]

Positional:
  project_dir          Path to the project folder (must contain footage/)

Options:
  --captions-only      Phase 1: transcribe + captions + optional burn-in
  --model MODEL        Override Whisper model (e.g. medium, large-v3)
  --client NAME        Load clients/<NAME>.json (glossary + preferences)
  --no-burn            Skip burning captions into video
  --debug              Verbose logging
```

### Examples

```powershell
# Basic
python main.py projects/myreel --captions-only

# Smaller model for low-RAM situations
python main.py projects/myreel --captions-only --model medium

# With a client glossary, no burn-in (just SRT + ASS)
python main.py projects/myreel --captions-only --client example_client --no-burn

# Debug everything
python main.py projects/myreel --captions-only --debug
```

---

## Running Tests

```powershell
# Install pytest (if not already in your venv)
pip install pytest

# Run all tests (no GPU, no API key required — all mocked)
pytest tests/ -v

# Run a specific test file
pytest tests/test_captions.py -v
```

All tests pass without a GPU or Gemini key — WhisperModel and the LLM are mocked.

---

## Configuration (`config.yaml`)

All tuneable settings live in `config.yaml` at the repo root.  Nothing is hardcoded.

Key sections:

| Section | Purpose |
|---|---|
| `whisper` | Model name, device, compute type, fallback settings, initial prompt |
| `llm` | Provider (`gemini`/`ollama`), model name, retry settings |
| `ffmpeg` | Encoder (`h264_nvenc`/`libx264`), preview resolution, final resolution |
| `captions` | Font, size, position, colors (ASS `&HAABBGGRR` format), words per line |

---

## Per-Client Presets (`clients/<name>.json`)

Save a client's preferences once; apply with `--client <name>`.

```json
{
  "name": "my_client",
  "preferences": { "default_preset": "product_review_reel" },
  "glossary": {
    "glow serum": "Glow Serum Pro",
    "brandname": "BrandName™"
  },
  "cta_variants": ["Follow karo for more reviews!"]
}
```

Brand terms in the glossary are always spelled correctly in captions,
even when Whisper or the LLM gets them wrong.

---

## How to Add a New Style Preset

1. Copy `presets/product_review_reel.json` → `presets/my_style.json`
2. Edit the fields you want to change (see schema below).
3. Use it: `python main.py projects/myreel --preset my_style` *(Phase 4+)*

**No code changes required.**  The entire style system is data-driven.

### Style Profile Schema (summary)

```jsonc
{
  "name": "my_style",
  "format":    { "aspect": "9:16", "resolution": "1080x1920", "fps": 30 },
  "pacing":    { "avg_shot_length_sec": 2.0, "remove_silence_over_sec": 0.25 },
  "structure": { "hook_first_3_sec": true, "cta_at_end": "Follow me!" },
  "broll":     { "frequency": "every_4_to_6_sec", "source": ["pexels"] },
  "sfx":       { "on_cut": "whoosh_soft", "volume": 0.5 },
  "music":     { "mood": "upbeat_trendy", "volume": 0.10 },
  "color":     { "lut": "bright_clean.cube", "intensity": 0.8 },
  "captions":  { "font": "Montserrat ExtraBold", "highlight_color": "#FFD400" }
}
```

Full schema with all fields and defaults: `presets/product_review_reel.json`

---

## Project Directory Structure

```
projects/<name>/
├── footage/               ← put raw clips here
├── script.txt             ← client script (Phase 3+)
├── brief.txt              ← plain-language style instruction (Phase 4+)
├── brand/                 ← logos, fonts, watermarks (Phase 5+)
├── cache/
│   ├── ingest/            ← ffprobe results (JSON)
│   ├── transcripts/       ← Whisper results (JSON, keyed by file hash)
│   └── llm/               ← LLM responses (JSON, keyed by prompt SHA-256)
└── output/
    ├── captions_en.srt
    ├── captions_hinglish.srt
    ├── captions_word_highlight.ass
    ├── final_captioned.mp4
    └── log.txt
```

---

## Caching

All expensive operations are cached and **never re-run on unchanged inputs**:

| Cache | Key | Location |
|---|---|---|
| ffprobe / ingest | MD5(path + size + mtime) | `cache/ingest/` |
| Whisper transcripts | MD5(path + size + mtime) | `cache/transcripts/` |
| LLM responses | SHA-256(prompt) | `cache/llm/` |

Delete a cache directory to force a fresh run.

---

## Build Phases

| Phase | Status | Description |
|---|---|---|
| **1** | ✅ **Done** | Ingest, transcribe, Hinglish captions, burn-in |
| 2 | ⏳ Next | Silence/filler removal, rough-cut preview |
| 3 | ⏳ | Script parser, aligner, take scorer, gap detector |
| 4 | ⏳ | Style system (schema, parser, planner, presets) |
| 5 | ⏳ | B-roll, audio (SFX/music/ducking), colour grading |
| 6 | ⏳ | Streamlit review UI + tweak loop |
| 7 | ⏳ | 9:16 reframe (MediaPipe), FCPXML/EDL export |
| 8 | ⏳ | Client presets, reference-video style learning, docs |

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `ffprobe not found` | Add FFmpeg `bin/` folder to your system PATH |
| `GEMINI_API_KEY not set` | Create `.env` from `.env.example` and add your key |
| `CUDA OOM / load failed` | Whisper auto-falls back to CPU `medium` model — no action needed |
| Captions burn fails | Ensure FFmpeg has libx264: `ffmpeg -encoders \| findstr libx264` |
| Empty transcript | Check clip has audio: look for `NO_AUDIO` flag in log |
| Devanagari in captions | Verify `GEMINI_API_KEY` is valid; Hinglish LLM pass requires it |
