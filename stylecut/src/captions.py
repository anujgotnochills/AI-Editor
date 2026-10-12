"""
Caption generation module.

Outputs
-------
``<stem>_en.srt``              Plain English SRT
``<stem>_hinglish.srt``        Roman-script Hinglish SRT
``<stem>_word_highlight.ass``  Styled ASS with per-word karaoke highlight
``<stem>_captioned.mp4``       Video with burned-in captions (optional)

ASS karaoke format
------------------
Each dialogue line uses ``{\\kf<centisecs>}word`` tags.
``\\kf`` sweeps a fill highlight left-to-right over the word's spoken
duration, creating the "active word" highlight effect familiar from
music-video and Indian reel captions.

ASS color format: ``&HAABBGGRR``  (AA=00 → fully opaque)
  Example: yellow #FFD400 → &H0000D4FF
"""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ─────────────────────────── time formatting ────────────────────────────────

def _srt_time(seconds: float) -> str:
    """Format seconds as ``HH:MM:SS,mmm`` (SRT spec)."""
    ms = round((seconds % 1) * 1000)
    s = int(seconds) % 60
    m = int(seconds) // 60 % 60
    h = int(seconds) // 3600
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _ass_time(seconds: float) -> str:
    """Format seconds as ``H:MM:SS.cc`` (ASS spec, centiseconds)."""
    cs = round((seconds % 1) * 100)
    s = int(seconds) % 60
    m = int(seconds) // 60 % 60
    h = int(seconds) // 3600
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


# ─────────────────────────── word grouping ───────────────────────────────────

def _group_words(
    words: list[dict[str, Any]],
    words_per_line: int = 3,
) -> list[dict[str, Any]]:
    """Group a flat word list into caption lines of *words_per_line* each.

    Each group dict: ``{"start": float, "end": float, "words": list}``.
    """
    lines: list[dict] = []
    for i in range(0, len(words), words_per_line):
        chunk = words[i : i + words_per_line]
        if not chunk:
            continue
        lines.append({
            "start": chunk[0]["start"],
            "end": chunk[-1]["end"],
            "words": chunk,
        })
    return lines


# ─────────────────────────── Caption Templates ───────────────────────────────

TEMPLATES: dict[str, dict[str, Any]] = {
    "hormozi": {
        "name": "Hormozi",
        "case": "upper",
        "srt_highlight_color": "#97DE25",  # Lime Green
        "font": "Anton",
        "size": 76,
        "highlight_color": "&H0025DE97",  # ASS &HAABBGGRR lime
        "text_color": "&H00FFFFFF",
        "outline_color": "&H00000000",
        "outline_size": 6,
        "shadow": 4,
    },
    "mrbeast": {
        "name": "MrBeast",
        "case": "upper",
        "srt_highlight_color": "#FFD400",  # Gold / Yellow
        "font": "Luckiest Guy",
        "size": 78,
        "highlight_color": "&H0000D4FF",  # ASS gold/yellow
        "text_color": "&H00FFFFFF",
        "outline_color": "&H00000000",
        "outline_size": 7,
        "shadow": 3,
    },
    "ali": {
        "name": "Ali Abdaal",
        "case": "clean",
        "srt_highlight_color": None,  # Clean aesthetic sans colors
        "font": "Poppins",
        "size": 64,
        "highlight_color": "&H00FFFFFF",
        "text_color": "&H00FFFFFF",
        "outline_color": "&H00202020",
        "outline_size": 2,
        "shadow": 1,
    },
    "neon": {
        "name": "Neon Glow",
        "case": "upper",
        "srt_highlight_color": "#00F0FF",  # Electric Cyan
        "font": "Montserrat ExtraBold",
        "size": 74,
        "highlight_color": "&H00FFF000",  # Cyan in ASS
        "text_color": "&H00FFFFFF",
        "outline_color": "&H007F00FF",  # Neon violet outline
        "outline_size": 5,
        "shadow": 6,
    },
    "skool": {
        "name": "Boxed Skool",
        "case": "upper",
        "srt_highlight_color": "#FF7A00",  # Skool Orange
        "font": "Montserrat ExtraBold",
        "size": 72,
        "highlight_color": "&H00007AFF",  # Orange in ASS
        "text_color": "&H00FFFFFF",
        "outline_color": "&H00000000",
        "outline_size": 4,
        "shadow": 2,
    },
    "hook": {
        "name": "Viral Hook (Golden Focus)",
        "case": "clean",
        "srt_highlight_color": "#FFD400",  # Golden Yellow
        "font": "Montserrat",
        "size": 58,
        "keyword_size": 130,
        "highlight_color": "&H00D4FF",  # Golden Yellow in ASS
        "text_color": "&H00FFFFFF",
        "outline_color": "&H00000000",
        "outline_size": 4,
        "shadow": 4,
    },
    "minimal": {
        "name": "Clean Minimal",
        "case": "clean",
        "srt_highlight_color": None,
        "font": "Helvetica Neue",
        "size": 60,
        "highlight_color": "&H00FFFFFF",
        "text_color": "&H00FFFFFF",
        "outline_color": "&H00000000",
        "outline_size": 2,
        "shadow": 2,
    },
}

_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with",
    "is", "was", "are", "were", "it", "this", "that", "i", "you", "he", "she", "we",
    "they", "my", "your", "his", "her", "so", "as", "if", "be", "do", "did", "have",
    "has", "had", "me", "him", "us", "them", "from", "by"
}


def _apply_template_to_chunk(
    words: list[str],
    template_name: str | None,
) -> list[str]:
    """Apply styling, casing, and keyword highlight coloring to words in a caption block."""
    if not template_name or template_name not in TEMPLATES:
        return words

    cfg = TEMPLATES[template_name]
    case_mode = cfg.get("case", "clean")
    color = cfg.get("srt_highlight_color")

    # 1. Apply casing
    formatted = [w.upper() if case_mode == "upper" else w for w in words]
    if not color or not formatted:
        return formatted

    # 2. Pick the most impactful word to highlight in this caption chunk
    candidates: list[tuple[int, str]] = []
    for i, w in enumerate(formatted):
        clean_w = w.lower().strip(".,!?:;\"'()[]{}")
        if clean_w not in _STOPWORDS and len(clean_w) >= 3:
            candidates.append((i, w))

    if candidates:
        target_idx = max(candidates, key=lambda item: len(item[1]))[0]
    else:
        target_idx = max(range(len(formatted)), key=lambda idx: len(formatted[idx]))

    target_word = formatted[target_idx]
    formatted[target_idx] = f'<font color="{color}"><b>{target_word}</b></font>'
    return formatted


# ─────────────────────────── SRT ────────────────────────────────────────────

def generate_srt(
    transcript: dict[str, Any],
    output_path: Path,
    *,
    use_hinglish: bool = False,
    words_per_line: int = 3,
    max_lines: int = 1,
    template: str | None = None,
) -> None:
    """Write an SRT file from a word-level transcript.

    Segments and natural pauses are respected so captions NEVER span across
    silences or cross sentence boundaries.

    Args:
        transcript:     Transcript dict from ``transcribe_clip()``.
        output_path:    Destination ``.srt`` file.
        use_hinglish:   If True, use ``word["word_hinglish"]`` fields.
        words_per_line: Caption width per line.
        max_lines:      Max lines per caption block (1 or 2).
        template:       Style template ("hormozi", "mrbeast", "ali", "neon", "skool", "minimal").
    """
    segments = transcript.get("segments", [])
    max_lines = max(1, min(int(max_lines), 2))
    chunk_size = max(1, words_per_line * max_lines)

    raw_items: list[tuple[float, float, str]] = []

    if segments:
        for seg in segments:
            seg_words = seg.get("words", [])
            if not seg_words:
                continue

            # Split words within segment if there is a silence pause > 0.5s or reaches chunk_size
            sub_chunks: list[list[dict[str, Any]]] = []
            curr_chunk: list[dict[str, Any]] = []

            for w in seg_words:
                if curr_chunk:
                    gap = w["start"] - curr_chunk[-1]["end"]
                    if gap > 0.5 or len(curr_chunk) >= chunk_size:
                        sub_chunks.append(curr_chunk)
                        curr_chunk = []
                curr_chunk.append(w)
            if curr_chunk:
                sub_chunks.append(curr_chunk)

            for chunk in sub_chunks:
                if not chunk:
                    continue
                if use_hinglish:
                    w_texts = [w.get("word_hinglish", w["word"]).strip() for w in chunk]
                else:
                    w_texts = [w["word"].strip() for w in chunk]

                w_texts = _apply_template_to_chunk(w_texts, template)

                if max_lines == 1:
                    text = " ".join(w_texts)
                elif max_lines > 1 and len(w_texts) > words_per_line:
                    l1 = " ".join(w_texts[:words_per_line])
                    l2 = " ".join(w_texts[words_per_line:])
                    text = f"{l1}\n{l2}"
                else:
                    text = " ".join(w_texts)

                start_t = float(chunk[0]["start"])
                end_t = float(chunk[-1]["end"])
                if end_t <= start_t:
                    end_t = start_t + 0.3

                raw_items.append((start_t, end_t, text))
    else:
        # Fallback if no segments list is present
        words = transcript.get("words", [])
        chunks = [words[i : i + chunk_size] for i in range(0, len(words), chunk_size)]
        for chunk in chunks:
            if not chunk:
                continue
            if use_hinglish:
                w_texts = [w.get("word_hinglish", w["word"]).strip() for w in chunk]
            else:
                w_texts = [w["word"].strip() for w in chunk]

            w_texts = _apply_template_to_chunk(w_texts, template)

            if max_lines == 1:
                text = " ".join(w_texts)
            elif max_lines > 1 and len(w_texts) > words_per_line:
                l1 = " ".join(w_texts[:words_per_line])
                l2 = " ".join(w_texts[words_per_line:])
                text = f"{l1}\n{l2}"
            else:
                text = " ".join(w_texts)
            start_t = float(chunk[0]["start"])
            end_t = float(chunk[-1]["end"])
            if end_t <= start_t:
                end_t = start_t + 0.3
            raw_items.append((start_t, end_t, text))

    # CRITICAL: Clean timestamps to guarantee strictly non-overlapping, chronological order!
    # Overlapping timestamps cause Premiere Pro to stack multiple subtitle entries vertically!
    cleaned_items: list[tuple[float, float, str]] = []
    for st, et, txt in raw_items:
        if cleaned_items:
            prev_st, prev_et, prev_txt = cleaned_items[-1]
            if prev_et >= st:
                # Clamp previous end time to 20ms before this start time so Premiere never stacks them
                new_prev_et = max(prev_st + 0.08, st - 0.02)
                cleaned_items[-1] = (prev_st, new_prev_et, prev_txt)
                if st <= new_prev_et:
                    st = new_prev_et + 0.02
        if et <= st:
            et = st + 0.25
        cleaned_items.append((st, et, txt))

    entries: list[str] = []
    for idx, (st, et, txt) in enumerate(cleaned_items, start=1):
        entries.append(
            f"{idx}\n"
            f"{_srt_time(st)} --> {_srt_time(et)}\n"
            f"{txt.strip()}\n"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(entries), encoding="utf-8")
    logger.info(f"[captions] SRT -> {output_path.name} ({len(entries)} blocks, max_lines={max_lines}, template={template})")


# ─────────────────────────── ASS ────────────────────────────────────────────

_ALIGNMENT_MAP = {"lower_third": 2, "upper": 8, "middle": 5}

_ASS_HEADER = """\
[Script Info]
ScriptType: v4.00+
PlayResX: {play_res_x}
PlayResY: {play_res_y}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{size},{text_color},{highlight_color},{outline_color},&HA0000000,-1,0,0,0,100,100,1,0,1,{outline_size},{shadow},{alignment},40,40,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def generate_ass(
    transcript: dict[str, Any],
    output_path: Path,
    *,
    font: str | None = None,
    size: int | None = None,
    highlight_color: str | None = None,
    text_color: str | None = None,
    outline_color: str | None = None,
    outline_size: int | None = None,
    shadow: int | None = None,
    position: str = "lower_third",
    words_per_line: int = 3,
    max_lines: int = 2,
    style: str = "word_popup",
    use_hinglish: bool = True,
    aspect: str = "9:16",
    template: str | None = None,
) -> None:
    """Write a styled ASS subtitle file.

    Supports two styles:
      - ``"word_popup"``: Words pop up one-by-one in exact audio sync,
        highlighting the current active word and wrapping across up to
        *max_lines* (default 2 lines).
      - ``"karaoke"``: Classic left-to-right sweep fill via ``{\\kf}`` tags.
    """
    tmpl_cfg = TEMPLATES.get(template, {}) if template else {}
    if tmpl_cfg:
        font = tmpl_cfg.get("font", font or "Montserrat ExtraBold")
        size = tmpl_cfg.get("size", size or 72)
        highlight_color = tmpl_cfg.get("highlight_color", highlight_color or "&H0000D4FF")
        text_color = tmpl_cfg.get("text_color", text_color or "&H00FFFFFF")
        outline_color = tmpl_cfg.get("outline_color", outline_color or "&H00000000")
        outline_size = tmpl_cfg.get("outline_size", outline_size if outline_size is not None else 4)
        shadow = tmpl_cfg.get("shadow", shadow if shadow is not None else 2)
    else:
        font = font or "Montserrat ExtraBold"
        size = size or 72
        highlight_color = highlight_color or "&H0000D4FF"
        text_color = text_color or "&H00FFFFFF"
        outline_color = outline_color or "&H00000000"
        outline_size = outline_size if outline_size is not None else 4
        shadow = shadow if shadow is not None else 2
    if aspect == "9:16":
        play_res_x, play_res_y, margin_v = 1080, 1920, 180
    elif aspect == "16:9":
        play_res_x, play_res_y, margin_v = 1920, 1080, 80
    else:
        play_res_x, play_res_y, margin_v = 1080, 1920, 180

    alignment = 5 if (position in ["middle", "center"] or template == "hook" or style == "viral_hook") else _ALIGNMENT_MAP.get(position, 2)

    header = _ASS_HEADER.format(
        play_res_x=play_res_x,
        play_res_y=play_res_y,
        font=font,
        size=size,
        text_color=text_color,
        highlight_color=highlight_color,
        outline_color=outline_color,
        outline_size=outline_size,
        shadow=shadow,
        alignment=alignment,
        margin_v=margin_v,
    )

    words = transcript.get("words", [])
    events: list[str] = []

    hl_tag = highlight_color if highlight_color.endswith("&") else f"{highlight_color}&"
    tx_tag = text_color if text_color.endswith("&") else f"{text_color}&"

    if style == "viral_hook" or template == "hook":
        # Viral hook layout (Reference image style):
        # Multi-tier layout with Lead words (white, 58pt), Huge Keyword (Golden Yellow, 125pt bold), Tail words (white, 48pt)
        chunk_size = max(2, words_per_line * max_lines)
        chunks = [words[i : i + chunk_size] for i in range(0, len(words), chunk_size)]
        for chunk in chunks:
            if not chunk:
                continue
            c_start = chunk[0]["start"]
            c_end = chunk[-1]["end"] + 0.15
            w_texts = [
                w.get("word_hinglish", w["word"]).strip()
                if use_hinglish else w["word"].strip()
                for w in chunk
            ]
            candidates = [
                (i, w) for i, w in enumerate(w_texts)
                if w.lower().strip(".,!?:;\"'()[]{}") not in _STOPWORDS and len(w.strip(".,!?:;\"'()[]{}")) >= 3
            ]
            if candidates:
                kw_idx = max(candidates, key=lambda cw: len(cw[1]))[0]
            else:
                kw_idx = max(range(len(w_texts)), key=lambda i: len(w_texts[i]))

            lead = " ".join(w_texts[:kw_idx])
            keyw = w_texts[kw_idx]
            tail = " ".join(w_texts[kw_idx + 1:])

            parts = []
            if lead:
                parts.append(f"{{\\fs58\\b0\\c{tx_tag}}}{lead}")
            parts.append(f"{{\\fs125\\b1\\c{hl_tag}}}{keyw}")
            if tail:
                parts.append(f"{{\\fs48\\b0\\c{tx_tag}}}{tail}")

            hook_text = "\\N".join(parts)
            events.append(
                f"Dialogue: 0,{_ass_time(c_start)},{_ass_time(c_end)},"
                f"Default,,0,0,0,,{hook_text}"
            )
    elif style == "karaoke":
        lines = _group_words(words, words_per_line)
        for line in lines:
            line_words = line["words"]
            line_start = line["start"]
            line_end = line["end"]

            parts: list[str] = []
            cursor = line_start

            for i, w in enumerate(line_words):
                word_text = (
                    w.get("word_hinglish", w["word"]).strip()
                    if use_hinglish
                    else w["word"].strip()
                )
                gap_cs = max(0, round((w["start"] - cursor) * 100))
                dur_cs = max(1, round((w["end"] - w["start"]) * 100))

                if gap_cs > 0:
                    parts.append(f"{{\\k{gap_cs}}}")

                parts.append(f"{{\\kf{dur_cs}}}{word_text}")
                if i < len(line_words) - 1:
                    parts.append(" ")
                cursor = w["end"]

            karaoke_text = "".join(parts)
            events.append(
                f"Dialogue: 0,{_ass_time(line_start)},{_ass_time(line_end)},"
                f"Default,,0,0,0,,{karaoke_text}"
            )
    else:
        # word_popup mode: words pop up in audio sync across up to max_lines lines
        chunk_size = max(1, words_per_line * max_lines)
        chunks = [words[i : i + chunk_size] for i in range(0, len(words), chunk_size)]

        hl_tag = highlight_color if highlight_color.endswith("&") else f"{highlight_color}&"
        tx_tag = text_color if text_color.endswith("&") else f"{text_color}&"

        for chunk in chunks:
            if not chunk:
                continue
            n_words = len(chunk)
            for k in range(n_words):
                cur_w = chunk[k]
                evt_start = cur_w["start"]
                if k + 1 < n_words:
                    evt_end = max(cur_w["end"], chunk[k + 1]["start"])
                else:
                    evt_end = cur_w["end"] + 0.35  # linger at end of chunk

                # Line 1 (up to words_per_line words)
                line1_tokens: list[str] = []
                limit1 = min(k + 1, words_per_line)
                for j in range(limit1):
                    w_text = (
                        chunk[j].get("word_hinglish", chunk[j]["word"]).strip()
                        if use_hinglish
                        else chunk[j]["word"].strip()
                    )
                    if j == k:
                        line1_tokens.append(f"{{\\c{hl_tag}\\fscx106\\fscy106}}{w_text}{{\\fscx100\\fscy100}}")
                    else:
                        line1_tokens.append(f"{{\\c{tx_tag}}}{w_text}")

                full_text = " ".join(line1_tokens)

                # Line 2 (remaining words if k >= words_per_line)
                if k >= words_per_line:
                    line2_tokens: list[str] = []
                    for j in range(words_per_line, k + 1):
                        w_text = (
                            chunk[j].get("word_hinglish", chunk[j]["word"]).strip()
                            if use_hinglish
                            else chunk[j]["word"].strip()
                        )
                        if j == k:
                            line2_tokens.append(f"{{\\c{hl_tag}\\fscx106\\fscy106}}{w_text}{{\\fscx100\\fscy100}}")
                        else:
                            line2_tokens.append(f"{{\\c{tx_tag}}}{w_text}")
                    full_text += "\\N" + " ".join(line2_tokens)

                events.append(
                    f"Dialogue: 0,{_ass_time(evt_start)},{_ass_time(evt_end)},"
                    f"Default,,0,0,0,,{full_text}"
                )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    logger.info(
        f"[captions] ASS -> {output_path.name} ({len(events)} dialogue events)"
    )


# ─────────────────────────── FFmpeg burn-in ─────────────────────────────────

def _ffmpeg_ass_path(ass_path: Path) -> str:
    """Return an FFmpeg-safe path string for the ``ass=`` filter.

    On Windows, drive-letter colons must be escaped: ``C:/`` → ``C\\:/``
    """
    p = str(ass_path.resolve()).replace("\\", "/")
    if sys.platform == "win32" and len(p) >= 2 and p[1] == ":":
        p = p[0] + r"\\:" + p[2:]
    return p


def burn_captions(
    video_path: Path,
    ass_path: Path,
    output_path: Path,
    *,
    encoder: str = "h264_nvenc",
    fallback_encoder: str = "libx264",
    preview: bool = False,
    preview_resolution: str = "960x540",
) -> None:
    """Burn ASS captions into *video_path* and write *output_path*.

    Tries *encoder* (NVENC) first; retries with *fallback_encoder* (libx264)
    on any non-zero exit code.

    Args:
        video_path:         Source video file.
        ass_path:           Styled ASS file to burn in.
        output_path:        Destination MP4 file.
        encoder:            Primary video encoder (``h264_nvenc``).
        fallback_encoder:   Fallback encoder (``libx264``).
        preview:            If True, scale to *preview_resolution* first.
        preview_resolution: WxH string used when *preview* is True.
    """
    safe_ass = _ffmpeg_ass_path(ass_path)
    vf = f"ass={safe_ass}"
    if preview:
        w, h = preview_resolution.split("x")
        vf = f"scale={w}:{h}," + vf

    def _run(enc: str) -> bool:
        if enc == "h264_nvenc":
            enc_flags = ["-c:v", enc, "-preset", "p4", "-cq", "23"]
        else:
            enc_flags = ["-c:v", enc, "-crf", "23", "-preset", "fast"]

        cmd = (
            ["ffmpeg", "-y", "-i", str(video_path), "-vf", vf, "-c:a", "copy"]
            + enc_flags
            + [str(output_path)]
        )
        logger.info(f"[captions] FFmpeg burn ({enc})")
        logger.debug("  " + " ".join(cmd))
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            logger.error(f"[captions] FFmpeg stderr:\n{res.stderr[-1000:]}")
        return res.returncode == 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not _run(encoder):
        logger.warning(
            f"[captions] {encoder} failed — retrying with {fallback_encoder}…"
        )
        if not _run(fallback_encoder):
            raise RuntimeError(
                f"Caption burn failed with both '{encoder}' and '{fallback_encoder}'.\n"
                "  Verify FFmpeg is installed and the encoder is supported:\n"
                "    ffmpeg -encoders | findstr h264"
            )
    logger.info(f"[captions] MP4  → {output_path.name}")


def render_transparent_overlay(
    ass_path: Path,
    output_mov_path: Path,
    duration: float,
    *,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
) -> Path:
    """Render ASS subtitle file to a transparent ProRes 4444 .mov video with alpha channel.

    This video can be placed directly on Premiere Pro Video Track 2 above footage,
    displaying exact viral typography, colors, and shadows without manual styling.
    """
    output_mov_path.parent.mkdir(parents=True, exist_ok=True)
    ass_filter_path = str(ass_path.resolve()).replace("\\", "/").replace(":", "\\:")
    
    # 1. Primary: QuickTime Animation (qtrle with argb) — 10x faster RLE compression on transparent pixels
    cmd_qtrle = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c=black@0.0:s={width}x{height}:r={fps}:d={max(1.0, duration):.2f}",
        "-vf", f"ass='{ass_filter_path}':alpha=1",
        "-c:v", "qtrle",
        "-pix_fmt", "argb",
        str(output_mov_path.resolve())
    ]
    logger.info(f"[captions] Rendering transparent overlay via qtrle ({width}x{height}, {duration:.1f}s): {output_mov_path.name}")
    res = subprocess.run(cmd_qtrle, capture_output=True, text=True)
    if res.returncode == 0 and output_mov_path.exists() and output_mov_path.stat().st_size > 1000:
        logger.info(f"[captions] Transparent overlay rendered ({output_mov_path.stat().st_size} bytes)")
        return output_mov_path

    # 2. Fallback: ProRes 4444 with alpha
    logger.warning(f"[captions] qtrle failed, falling back to ProRes 4444: {res.stderr[-200:]}")
    cmd_prores = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c=black@0.0:s={width}x{height}:r={fps}:d={max(1.0, duration):.2f}",
        "-vf", f"ass='{ass_filter_path}':alpha=1",
        "-c:v", "prores_ks",
        "-profile:v", "4",
        "-pix_fmt", "yuva444p10le",
        str(output_mov_path.resolve())
    ]
    res2 = subprocess.run(cmd_prores, capture_output=True, text=True)
    if res2.returncode != 0:
        logger.error(f"[captions] Transparent render failed: {res2.stderr[-400:]}")
        raise RuntimeError(f"FFmpeg overlay render failed: {res2.stderr[-300:]}")
    return output_mov_path
