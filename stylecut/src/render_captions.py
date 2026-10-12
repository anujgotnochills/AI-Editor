"""render_captions.py — Remotion-powered template caption renderer.

Splits transcripts into shared captions.json spec, renders each line as an
isolated transparent ProRes 4444 (.mov with alpha) clip with caching and
concurrency, and produces captions_manifest.json for Premiere Pro timeline placement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("stylecut.render_captions")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with",
    "is", "was", "are", "were", "it", "this", "that", "i", "you", "he", "she", "we",
    "they", "my", "your", "his", "her", "so", "as", "if", "be", "do", "did", "have",
    "has", "had", "me", "him", "us", "them", "from", "by"
}


def create_captions_spec(
    transcript: dict[str, Any],
    template_id: str = "hook_gold",
    canvas: Optional[dict[str, Any]] = None,
    style_overrides: Optional[dict[str, Any]] = None,
    words_per_line: int = 3,
    max_words_per_line: int = 3,
    use_hinglish: bool = True,
) -> dict[str, Any]:
    """Build shared captions.json spec from transcription data."""
    words = transcript.get("words", [])
    canvas_cfg = canvas or {"width": 1080, "height": 1920, "fps": 30}
    fps = canvas_cfg.get("fps", 30)

    lines: list[dict[str, Any]] = []
    chunk_size = max(1, words_per_line)

    for i in range(0, len(words), chunk_size):
        chunk = words[i : i + chunk_size]
        if not chunk:
            continue

        start_time = float(chunk[0]["start"])
        end_time = float(chunk[-1]["end"])
        # Slight visual buffer for very short lines
        duration = max(0.4, round(end_time - start_time, 3))
        end_time = round(start_time + duration, 3)

        line_words = []
        for w in chunk:
            w_text = (
                w.get("word_hinglish", w["word"]).strip()
                if use_hinglish
                else w["word"].strip()
            )
            line_words.append({
                "word": w_text,
                "start": round(float(w["start"]), 3),
                "end": round(float(w["end"]), 3),
                "highlight": False,
            })

        # Pick candidate emphatic keyword
        candidates = [
            (idx, lw["word"])
            for idx, lw in enumerate(line_words)
            if lw["word"].lower().strip(".,!?:;\"'()[]{}") not in _STOPWORDS
            and len(lw["word"].strip(".,!?:;\"'()[]{}")) >= 3
        ]
        if candidates:
            kw_idx, kw_text = max(candidates, key=lambda c: len(c[1]))
        elif line_words:
            kw_idx = max(range(len(line_words)), key=lambda idx: len(line_words[idx]["word"]))
            kw_text = line_words[kw_idx]["word"]
        else:
            kw_idx = -1
            kw_text = ""

        if kw_idx >= 0 and kw_idx < len(line_words):
            line_words[kw_idx]["highlight"] = True

        full_text = " ".join(lw["word"] for lw in line_words)
        lead = " ".join(lw["word"] for lw in line_words[:kw_idx]) if kw_idx > 0 else ""
        tail = " ".join(lw["word"] for lw in line_words[kw_idx + 1:]) if kw_idx >= 0 and kw_idx + 1 < len(line_words) else ""

        line_id = f"line_{len(lines)}"
        lines.append({
            "id": line_id,
            "text": full_text,
            "start": round(start_time, 3),
            "end": round(end_time, 3),
            "duration": round(duration, 3),
            "lead": lead,
            "keyword": kw_text,
            "tail": tail,
            "emphasis": kw_text,
            "words": line_words,
        })

    return {
        "templateId": template_id,
        "canvas": canvas_cfg,
        "styleOverrides": style_overrides or {},
        "wordsPerLine": words_per_line,
        "maxWordsPerLine": max_words_per_line,
        "lines": lines,
    }


def compute_line_hash(
    template_id: str,
    style_overrides: dict,
    line: dict,
    canvas: Optional[dict] = None,
) -> str:
    """Compute deterministic SHA256 hash of line content, timing, and style."""
    data = {
        "templateId": template_id,
        "style": style_overrides,
        "canvas": {"width": canvas.get("width", 1080), "height": canvas.get("height", 1920)} if canvas else {},
        "text": line.get("text", ""),
        "keyword": line.get("keyword", ""),
        "duration": line.get("duration", 0),
        "words": line.get("words", []),
    }
    dumped = json.dumps(data, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(dumped.encode("utf-8")).hexdigest()[:12]


def render_single_line(
    engine_dir: Path,
    template_id: str,
    canvas: dict[str, Any],
    style_overrides: dict[str, Any],
    line: dict[str, Any],
    output_dir: Path,
    cache_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """Render a single line into a transparent ProRes 4444 .mov clip with caching."""
    line_hash = compute_line_hash(template_id, style_overrides, line, canvas=canvas)
    line_id = line["id"]
    output_dir.mkdir(parents=True, exist_ok=True)
    out_clip = output_dir / f"{line_id}_{line_hash}.mov"

    # Check cache
    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached_file = cache_dir / f"{line_hash}.mov"
        if cached_file.exists() and cached_file.stat().st_size > 1000:
            logger.info(f"[cache hit] {line_id} -> {cached_file.name}")
            if not out_clip.exists():
                import shutil
                shutil.copyfile(cached_file, out_clip)
            return {
                "lineId": line_id,
                "text": line["text"],
                "path": str(out_clip.resolve()),
                "start": line["start"],
                "end": line["end"],
                "duration": line["duration"],
                "track": 2,
                "cached": True,
            }

    if out_clip.exists() and out_clip.stat().st_size > 1000:
        logger.info(f"[cache hit] {line_id} -> {out_clip.name}")
        return {
            "lineId": line_id,
            "text": line["text"],
            "path": str(out_clip.resolve()),
            "start": line["start"],
            "end": line["end"],
            "duration": line["duration"],
            "track": 2,
            "cached": True,
        }

def ensure_remotion_bundle(engine_dir: Path) -> str:
    """Pre-bundle Remotion code once so per-line renders don't re-compile Webpack."""
    bundle_dir = engine_dir / ".remotion_bundle"
    index_file = engine_dir / "src" / "index.ts"

    # Re-bundle if missing or if source file is newer
    needs_bundle = not bundle_dir.exists()
    if not needs_bundle and index_file.exists():
        src_mtime = max(p.stat().st_mtime for p in (engine_dir / "src").glob("**/*") if p.is_file())
        bundle_mtime = (bundle_dir / "index.html").stat().st_mtime if (bundle_dir / "index.html").exists() else 0
        if src_mtime > bundle_mtime:
            needs_bundle = True

    if needs_bundle:
        logger.info("Pre-bundling Remotion project once for fast line renders...")
        cmd = ["npx", "remotion", "bundle", "src/index.ts", f"--out-dir={bundle_dir.name}", "--bundle-cache"]
        res = subprocess.run(cmd, cwd=str(engine_dir), capture_output=True, text=True, shell=True)
        if res.returncode == 0:
            logger.info("Remotion bundle created successfully.")
        else:
            logger.warning(f"Remotion bundling warning: {res.stderr[:200]}")
            return "src/index.ts"

    return bundle_dir.name if bundle_dir.exists() else "src/index.ts"


def render_single_line(
    engine_dir: Path,
    template_id: str,
    canvas: dict[str, Any],
    style_overrides: dict[str, Any],
    line: dict[str, Any],
    output_dir: Path,
    cache_dir: Optional[Path] = None,
    entry_point: str = ".remotion_bundle",
) -> dict[str, Any]:
    """Render a single line into a transparent ProRes 4444 .mov clip with caching."""
    line_hash = compute_line_hash(template_id, style_overrides, line)
    line_id = line["id"]
    output_dir.mkdir(parents=True, exist_ok=True)
    out_clip = output_dir / f"{line_id}_{line_hash}.mov"

    # Check cache
    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached_file = cache_dir / f"{line_hash}.mov"
        if cached_file.exists() and cached_file.stat().st_size > 1000:
            logger.info(f"[cache hit] {line_id} -> {cached_file.name}")
            if not out_clip.exists():
                import shutil
                shutil.copyfile(cached_file, out_clip)
            return {
                "lineId": line_id,
                "text": line["text"],
                "path": str(out_clip.resolve()),
                "start": line["start"],
                "end": line["end"],
                "duration": line["duration"],
                "track": 2,
                "cached": True,
            }

    if out_clip.exists() and out_clip.stat().st_size > 1000:
        logger.info(f"[cache hit] {line_id} -> {out_clip.name}")
        return {
            "lineId": line_id,
            "text": line["text"],
            "path": str(out_clip.resolve()),
            "start": line["start"],
            "end": line["end"],
            "duration": line["duration"],
            "track": 2,
            "cached": True,
        }

    # Write temporary line props JSON
    props = {
        "templateId": template_id,
        "line": line,
        "style": style_overrides,
        "canvas": canvas,
    }
    props_file = output_dir / f"{line_id}_props.json"
    props_file.write_text(json.dumps(props, ensure_ascii=False, indent=2), encoding="utf-8")

    # Command using Remotion CLI with GPU angle acceleration
    cmd = [
        "npx", "remotion", "render",
        entry_point,
        "CaptionLine",
        str(out_clip.resolve()),
        f"--props={str(props_file.resolve())}",
        "--codec=prores",
        "--prores-profile=4444",
        "--pixel-format=yuva444p10le",
        "--gl=angle",
        "--bundle-cache",
    ]

    logger.info(f"[render line] {line_id} ({line['duration']}s): {out_clip.name}")
    res = subprocess.run(cmd, cwd=str(engine_dir), capture_output=True, text=True, shell=True)

    try:
        props_file.unlink(missing_ok=True)
    except Exception:
        pass

    if res.returncode != 0 or not out_clip.exists() or out_clip.stat().st_size < 1000:
        logger.error(f"[render fail] {line_id}: {res.stderr[-400:]}")
        raise RuntimeError(f"Failed to render line {line_id}: {res.stderr[-300:]}")

    if cache_dir and out_clip.exists():
        import shutil
        shutil.copyfile(out_clip, cache_dir / f"{line_hash}.mov")

    logger.info(f"[render done] {line_id} ({out_clip.stat().st_size} bytes)")
    return {
        "lineId": line_id,
        "text": line["text"],
        "path": str(out_clip.resolve()),
        "start": line["start"],
        "end": line["end"],
        "duration": line["duration"],
        "track": 2,
        "cached": False,
    }


def render_captions_manifest(
    spec: dict[str, Any],
    output_dir: Path,
    engine_dir: Optional[Path] = None,
    concurrency: int = 4,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> dict[str, Any]:
    """Render all lines in captions spec concurrently into clips & return manifest."""
    if not engine_dir:
        engine_dir = Path(__file__).resolve().parent.parent / "captions_engine"

    template_id = spec.get("templateId", "hook_gold")
    canvas = spec.get("canvas", {"width": 1080, "height": 1920, "fps": 30})
    style_overrides = spec.get("styleOverrides", {})
    lines = spec.get("lines", [])

    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = Path.home() / ".stylecut" / "cache" / "remotion"

    # Pre-bundle to avoid compiling Webpack per line
    entry_point = ensure_remotion_bundle(engine_dir)

    # Save captions.json
    spec_path = output_dir / "captions.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")

    clips: list[dict[str, Any]] = []

    # Concurrency using ThreadPoolExecutor
    workers = max(1, min(concurrency, len(lines)))
    logger.info(f"Rendering {len(lines)} caption clips with concurrency={workers}")

    completed_count = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_map = {
            pool.submit(
                render_single_line,
                engine_dir,
                template_id,
                canvas,
                style_overrides,
                line,
                output_dir,
                cache_dir,
                entry_point,
            ): line
            for line in lines
        }

        for future in as_completed(future_map):
            clip_res = future.result()
            clips.append(clip_res)
            completed_count += 1
            if progress_callback:
                try:
                    progress_callback(completed_count, len(lines))
                except Exception:
                    pass

    # Sort clips chronologically by start time
    clips.sort(key=lambda c: c["start"])

    manifest_path = output_dir / "captions_manifest.json"
    manifest = {
        "templateId": template_id,
        "track": 2,
        "totalClips": len(clips),
        "clips": clips,
        "manifest_path": str(manifest_path.resolve()),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"Generated manifest: {manifest_path} ({len(clips)} clips)")

    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Render captions using Remotion")
    parser.add_argument("spec_file", type=Path, help="Path to captions.json")
    parser.add_argument("--out-dir", type=Path, default=Path("./rendered_captions"))
    parser.add_argument("--concurrency", type=int, default=3)
    args = parser.parse_args()

    with open(args.spec_file, "r", encoding="utf-8") as f:
        spec_data = json.load(f)

    manifest = render_captions_manifest(spec_data, args.out_dir, concurrency=args.concurrency)
    print(f"Done: {manifest['totalClips']} clips rendered.")
