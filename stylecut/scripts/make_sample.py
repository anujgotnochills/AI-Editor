"""
Generate a synthetic sample project for testing StyleCut Phase 1.

What it creates
---------------
projects/sample/
├── footage/sample_clip.mp4   — 25-second clip (speech audio via gTTS if available,
│                               otherwise a 440 Hz tone so the audio track exists)
├── script.txt                — sample Hinglish script with hook/body/CTA
└── brief.txt                 — sample style brief for the LLM

Usage
-----
    python scripts/make_sample.py

Then test Phase 1:
    python main.py projects/sample --captions-only

Dependencies
------------
    pip install gtts          (optional — only needed for real speech audio)
    ffmpeg on PATH            (required)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Ensure standard Windows ffmpeg path is on PATH if not present
if not shutil.which("ffmpeg"):
    for candidate in [r"C:\ffmpeg\bin"]:
        if os.path.exists(os.path.join(candidate, "ffmpeg.exe")):
            os.environ["PATH"] = candidate + os.pathsep + os.environ.get("PATH", "")
            break

_REPO_ROOT = Path(__file__).parent.parent.resolve()
_SAMPLE_DIR = _REPO_ROOT / "projects" / "sample"
_FOOTAGE_DIR = _SAMPLE_DIR / "footage"

# ─────────────────────────── sample content ──────────────────────────────────

_SCRIPT = """\
Hook: Yaar, ek cheez try ki maine aaj jo bilkul game changer hai!
Body: Is product ki quality dekho — it's absolutely amazing.
Body: Price bhi kaafi reasonable hai, full value milti hai.
Body: Main use kar raha tha pichle ek hafte se, aur results bohot achhe hain.
CTA: Follow karo aur share karo agar tumhe bhi ye pasand aaya!
"""

_BRIEF = """\
Instagram product review reel, fast and punchy, Hinglish captions with yellow
word highlight, upbeat low-volume background music, bright clean color grade,
end with a follow CTA. Max 45 seconds, 9:16 vertical format.
"""

# Text fed to gTTS for the audio track (Hindi-accent friendly)
_TTS_TEXT = (
    "Yaar, ek cheez try ki maine aaj jo bilkul game changer hai! "
    "Is product ki quality dekho — it's absolutely amazing. "
    "Price bhi kaafi reasonable hai, full value milti hai. "
    "Main use kar raha tha pichle ek hafte se, aur results bohot achhe hain. "
    "Follow karo aur share karo agar tumhe bhi ye pasand aaya!"
)


# ─────────────────────────── TTS helper ──────────────────────────────────────

def _make_tts_audio(out_path: Path) -> bool:
    """Generate TTS audio with gTTS.  Returns True on success."""
    try:
        from gtts import gTTS
        tts = gTTS(text=_TTS_TEXT, lang="hi", slow=False)
        tts.save(str(out_path))
        print(f"[sample] TTS audio saved: {out_path.name}")
        return True
    except ImportError:
        print(
            "[sample] gTTS not installed — using silent tone instead.\n"
            "         For real speech: pip install gtts"
        )
        return False
    except Exception as exc:
        print(f"[sample] gTTS failed ({exc}) — using silent tone instead.")
        return False


# ─────────────────────────── FFmpeg helpers ───────────────────────────────────

def _check_ffmpeg() -> None:
    r = subprocess.run(["ffmpeg", "-version"], capture_output=True)
    if r.returncode != 0:
        print(
            "ERROR: ffmpeg not found on PATH.\n"
            "  Windows: https://www.gyan.dev/ffmpeg/builds/\n"
            "  Add ffmpeg/bin to your PATH and retry.",
            file=sys.stderr,
        )
        sys.exit(1)


def _make_video(audio_path: Path | None, output_path: Path) -> None:
    """Compose a 25-second vertical test video using FFmpeg lavfi sources."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 1080×1920 dark-blue background + centred text overlay
    video_src = (
        "color=c=0x1A1A2E:size=1080x1920:duration=25:rate=30,"
        "drawtext=text='StyleCut\\ Sample\\ Clip':"
        "fontsize=72:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2"
    )

    if audio_path and audio_path.exists():
        # Use TTS audio (pad/trim to 25 s)
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"[v]{video_src}[v_out]",  # placeholder
            "-i", str(audio_path),
            "-filter_complex", f"[0]null[vout]",
            "-map", "[vout]", "-map", "1:a",
            "-c:v", "libx264", "-crf", "28", "-preset", "fast",
            "-c:a", "aac", "-b:a", "128k",
            "-t", "25",
            str(output_path),
        ]
        # Simpler form that avoids filter_complex complexity:
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", video_src,
            "-i", str(audio_path),
            "-c:v", "libx264", "-crf", "28", "-preset", "fast",
            "-c:a", "aac", "-b:a", "128k",
            "-t", "25", "-shortest",
            str(output_path),
        ]
    else:
        # 440 Hz sine tone so the clip has an audio track Whisper can analyse
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", video_src,
            "-f", "lavfi", "-i", "sine=frequency=440:duration=25",
            "-c:v", "libx264", "-crf", "28", "-preset", "fast",
            "-c:a", "aac", "-b:a", "64k",
            "-t", "25",
            str(output_path),
        ]

    print(f"[sample] Building video with FFmpeg…")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[sample] FFmpeg error:\n{result.stderr[-800:]}", file=sys.stderr)
        raise RuntimeError("FFmpeg failed to create the sample clip.")
    print(f"[sample] Video ready: {output_path}")


# ─────────────────────────── main ────────────────────────────────────────────

def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    print("=== StyleCut Sample Generator ===")
    _check_ffmpeg()

    _FOOTAGE_DIR.mkdir(parents=True, exist_ok=True)

    # Write text files
    (_SAMPLE_DIR / "script.txt").write_text(_SCRIPT, encoding="utf-8")
    (_SAMPLE_DIR / "brief.txt").write_text(_BRIEF, encoding="utf-8")
    print(f"[sample] Written: script.txt  brief.txt  →  {_SAMPLE_DIR}")

    # Generate TTS audio into a temp file
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp_audio = Path(tmp.name)

    tts_ok = _make_tts_audio(tmp_audio)
    audio = tmp_audio if tts_ok else None

    # Build the video
    video_out = _FOOTAGE_DIR / "sample_clip.mp4"
    _make_video(audio, video_out)

    # Cleanup temp
    if tmp_audio.exists():
        tmp_audio.unlink(missing_ok=True)

    print()
    print("✅  Sample project ready!")
    print(f"   Project dir : {_SAMPLE_DIR}")
    print(f"   Clip        : {video_out}")
    if not tts_ok:
        print()
        print("ℹ  The clip uses a sine tone, not speech.")
        print("   Whisper will produce an empty/nonsense transcript.")
        print("   For a real acceptance test, drop your own clip into:")
        print(f"   {_FOOTAGE_DIR}")
    print()
    print("Run Phase 1:")
    rel = _SAMPLE_DIR.relative_to(_REPO_ROOT)
    print(f"  python main.py {rel} --captions-only")


if __name__ == "__main__":
    main()
