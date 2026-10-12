"""
StyleCut — CLI entry point.

Usage (Phase 1):
    python main.py projects/<name> --captions-only
    python main.py projects/<name> --captions-only --model medium
    python main.py projects/<name> --captions-only --client example_client --no-burn
    python main.py projects/<name> --captions-only --debug
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from pathlib import Path

# ── ensure src/ is importable regardless of working directory ────────────────
_REPO_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(_REPO_ROOT))

from src.utils import load_config, load_env, setup_logging
from src.project import ProjectContext

logger = logging.getLogger("stylecut")

_CONFIG_PATH = _REPO_ROOT / "config.yaml"


# ─────────────────────────── CLI ─────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="stylecut",
        description="StyleCut — AI Video Editing Automation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python main.py projects/myreel --captions-only\n"
            "  python main.py projects/myreel --captions-only --model medium --no-burn\n"
            "  python main.py projects/myreel --captions-only --client my_client\n"
        ),
    )
    p.add_argument("project_dir", help="Path to the project folder (must contain footage/).")
    p.add_argument(
        "--captions-only",
        action="store_true",
        help="Phase 1: transcribe + generate SRT/ASS captions, optionally burn into video.",
    )
    p.add_argument(
        "--rough-cut",
        action="store_true",
        help="Phase 2: silence & filler removal → rough_cut.mp4 + re-timed captions.",
    )
    p.add_argument(
        "--model",
        default=None,
        metavar="MODEL",
        help="Override Whisper model (e.g. medium, large-v3, distil-large-v3).",
    )
    p.add_argument(
        "--client",
        default=None,
        metavar="NAME",
        help="Client preset name (without .json) in the clients/ folder.",
    )
    p.add_argument(
        "--preset",
        default=None,
        metavar="NAME",
        help="Style preset name (without .json) in the presets/ folder. (Phase 4+)",
    )
    p.add_argument(
        "--style-file",
        default=None,
        metavar="PATH",
        help="Plain-text style instruction file. (Phase 4+)",
    )
    p.add_argument(
        "--no-burn",
        action="store_true",
        help="Skip burning captions into video (output SRT/ASS only).",
    )
    p.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose debug logging.",
    )
    return p


# ─────────────────────────── helpers ─────────────────────────────────────────

def _load_client(name: str) -> dict:
    path = _REPO_ROOT / "clients" / f"{name}.json"
    if not path.exists():
        logger.warning(f"Client preset not found: {path}  (continuing without it)")
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    logger.info(f"[main] Loaded client preset: {name}")
    return data


def _print_clip_table(clips: list[dict]) -> None:
    """Log a readable summary of ingested clips."""
    logger.info(f"{'Clip':<30} {'Duration':>8} {'Resolution':>12} {'FPS':>6}  Flags")
    logger.info("-" * 72)
    for c in clips:
        res = f"{c['width']}x{c['height']}"
        flags = ", ".join(c.get("flags", [])) or "OK"
        logger.info(
            f"{c['filename']:<30} {c['duration_sec']:>7.1f}s {res:>12} "
            f"{c['fps']:>5.2f}  {flags}"
        )


# ─────────────────────────── Phase 1 pipeline ────────────────────────────────

def _run_captions(
    ctx: ProjectContext,
    cfg: dict,
    client: dict,
    args: argparse.Namespace,
) -> None:
    """Phase 1: ingest → transcribe → Hinglish → SRT/ASS → optional burn-in."""
    from src.ingest import ingest
    from src.transcribe import transcribe_project
    from src.hinglish import convert_transcript_to_hinglish
    from src.captions import generate_srt, generate_ass, burn_captions
    from src.llm.base import get_provider

    # ── 1. Ingest ──────────────────────────────────────────────────────────────
    logger.info("─── Step 1/4: Ingest ───────────────────────────────────────────")
    clips = ingest(ctx.footage_dir, ctx.ingest_cache_dir)
    if not clips:
        logger.error(
            f"No video files found in {ctx.footage_dir}\n"
            "  Supported formats: .mp4 .mov .avi .mkv .mts .m2ts .webm .m4v"
        )
        sys.exit(1)
    _print_clip_table(clips)

    # ── 2. Transcribe ──────────────────────────────────────────────────────────
    logger.info("─── Step 2/4: Transcribe ───────────────────────────────────────")
    whisper_cfg: dict = dict(cfg.get("whisper", {}))
    if args.model:
        whisper_cfg["model"] = args.model
        logger.info(f"[main] Whisper model overridden to: {args.model}")

    transcripts = transcribe_project(clips, ctx.transcript_cache_dir, whisper_cfg)
    if not transcripts:
        logger.error(
            "No clips could be transcribed.\n"
            "  Check that your clips have an audio track (not flagged NO_AUDIO)."
        )
        sys.exit(1)

    # ── 3. Per-clip captions ───────────────────────────────────────────────────
    logger.info("─── Step 3/4: Hinglish + Captions ─────────────────────────────")

    cap_cfg: dict = cfg.get("captions", {})
    ffmpeg_cfg: dict = cfg.get("ffmpeg", {})
    llm_cfg: dict = cfg.get("llm", {})
    glossary: dict[str, str] = client.get("glossary", {})

    hinglish_enabled = cap_cfg.get("hinglish_enabled", True)
    burn = cap_cfg.get("burn_in", True) and not args.no_burn

    # Instantiate LLM only when needed (avoids wasting Gemini quota)
    llm = None
    if hinglish_enabled:
        llm = get_provider(llm_cfg, ctx.llm_cache_dir)

    for filename, transcript in transcripts.items():
        stem = Path(filename).stem
        logger.info(f"  Processing: {filename}")

        # Hinglish conversion
        if hinglish_enabled and llm is not None:
            h_transcript = convert_transcript_to_hinglish(transcript, llm, glossary)
        else:
            h_transcript = transcript

        # English SRT
        en_srt = ctx.output_dir / f"{stem}_en.srt"
        generate_srt(
            transcript,
            en_srt,
            use_hinglish=False,
            words_per_line=cap_cfg.get("words_per_line", 3),
            max_lines=int(cap_cfg.get("max_lines", 2)),
        )

        # Hinglish SRT
        hi_srt = ctx.output_dir / f"{stem}_hinglish.srt"
        generate_srt(
            h_transcript,
            hi_srt,
            use_hinglish=True,
            words_per_line=cap_cfg.get("words_per_line", 3),
            max_lines=int(cap_cfg.get("max_lines", 2)),
        )

        # Styled ASS (word highlight)
        ass_out = ctx.output_dir / f"{stem}_word_highlight.ass"
        generate_ass(
            h_transcript,
            ass_out,
            font=cap_cfg.get("font", "Montserrat ExtraBold"),
            size=int(cap_cfg.get("size", 72)),
            highlight_color=cap_cfg.get("highlight_color", "&H0000D4FF"),
            text_color=cap_cfg.get("text_color", "&H00FFFFFF"),
            outline_color=cap_cfg.get("outline_color", "&H00000000"),
            outline_size=int(cap_cfg.get("outline_size", 4)),
            shadow=int(cap_cfg.get("shadow", 2)),
            position=cap_cfg.get("position", "lower_third"),
            words_per_line=int(cap_cfg.get("words_per_line", 3)),
            max_lines=int(cap_cfg.get("max_lines", 2)),
            style=cap_cfg.get("style", "word_popup"),
            use_hinglish=hinglish_enabled,
        )

        # Burn-in
        if burn:
            clip_meta = next(
                (c for c in clips if c["filename"] == filename), None
            )
            if clip_meta:
                video_path = Path(clip_meta["path"])
                burned_out = ctx.output_dir / f"{stem}_captioned.mp4"
                burn_captions(
                    video_path,
                    ass_out,
                    burned_out,
                    encoder=ffmpeg_cfg.get("video_encoder", "h264_nvenc"),
                    fallback_encoder=ffmpeg_cfg.get("fallback_encoder", "libx264"),
                )
            else:
                logger.warning(f"[main] Could not find clip metadata for {filename}")

    # ── 4. Convenience copies for single-clip projects ─────────────────────────
    logger.info("─── Step 4/4: Finalise ─────────────────────────────────────────")
    if len(transcripts) == 1:
        stem = Path(next(iter(transcripts))).stem
        for src_name, dst_name in [
            (f"{stem}_en.srt",            "captions_en.srt"),
            (f"{stem}_hinglish.srt",      "captions_hinglish.srt"),
            (f"{stem}_word_highlight.ass", "captions_word_highlight.ass"),
            (f"{stem}_captioned.mp4",     "final_captioned.mp4"),
        ]:
            src = ctx.output_dir / src_name
            dst = ctx.output_dir / dst_name
            if src.exists() and not dst.exists():
                shutil.copy2(src, dst)
                logger.debug(f"[main] Copied {src_name} → {dst_name}")

    logger.info(f"✅  Done.  Outputs in: {ctx.output_dir}")
    # List output files
    outputs = sorted(ctx.output_dir.glob("*"))
    for f in outputs:
        if f.name != "log.txt":
            logger.info(f"   {f.name}")


# ─────────────────────────── Phase 2 pipeline ────────────────────────────────

def _run_rough_cut(
    ctx: ProjectContext,
    cfg: dict,
    client: dict,
    args: argparse.Namespace,
) -> None:
    """Phase 2: ingest → transcribe → Hinglish → silence & filler removal → remapped captions."""
    from src.ingest import ingest
    from src.transcribe import transcribe_project
    from src.hinglish import convert_transcript_to_hinglish
    from src.captions import generate_srt, generate_ass, burn_captions
    from src.cutter import build_keep_segments, remap_transcript_to_cuts, render_rough_cut
    from src.llm.base import get_provider

    # ── 1. Ingest ──────────────────────────────────────────────────────────────
    logger.info("─── Step 1/5: Ingest ───────────────────────────────────────────")
    clips = ingest(ctx.footage_dir, ctx.ingest_cache_dir)
    if not clips:
        logger.error(
            f"No video files found in {ctx.footage_dir}\n"
            "  Supported formats: .mp4 .mov .avi .mkv .mts .m2ts .webm .m4v"
        )
        sys.exit(1)
    _print_clip_table(clips)

    # ── 2. Transcribe ──────────────────────────────────────────────────────────
    logger.info("─── Step 2/5: Transcribe ───────────────────────────────────────")
    whisper_cfg: dict = dict(cfg.get("whisper", {}))
    if args.model:
        whisper_cfg["model"] = args.model
        logger.info(f"[main] Whisper model overridden to: {args.model}")

    transcripts = transcribe_project(clips, ctx.transcript_cache_dir, whisper_cfg)
    if not transcripts:
        logger.error(
            "No clips could be transcribed.\n"
            "  Check that your clips have an audio track (not flagged NO_AUDIO)."
        )
        sys.exit(1)

    # ── 3. Hinglish transliteration ───────────────────────────────────────────
    logger.info("─── Step 3/5: Hinglish Transliteration ─────────────────────────")
    cap_cfg: dict = cfg.get("captions", {})
    ffmpeg_cfg: dict = cfg.get("ffmpeg", {})
    pacing_cfg: dict = cfg.get("pacing", {})
    llm_cfg: dict = cfg.get("llm", {})
    glossary: dict[str, str] = client.get("glossary", {})

    hinglish_enabled = cap_cfg.get("hinglish_enabled", True)
    burn = cap_cfg.get("burn_in", True) and not args.no_burn

    llm = None
    if hinglish_enabled:
        llm = get_provider(llm_cfg, ctx.llm_cache_dir)

    min_silence_sec = float(pacing_cfg.get("remove_silence_over_sec", 0.25))
    padding_ms = float(pacing_cfg.get("padding_ms", 50.0))
    fillers = pacing_cfg.get("fillers", [])

    # ── 4. Rough Cut & Captions ────────────────────────────────────────────────
    logger.info("─── Step 4/5: Silence & Filler Removal (Rough Cut) ───────────")
    for filename, transcript in transcripts.items():
        stem = Path(filename).stem
        logger.info(f"  Processing: {filename}")

        if hinglish_enabled and llm is not None:
            h_transcript = convert_transcript_to_hinglish(transcript, llm, glossary)
        else:
            h_transcript = transcript

        clip_meta = next((c for c in clips if c["filename"] == filename), None)
        clip_duration = transcript.get("duration_sec", 0.0)
        if clip_meta and clip_meta.get("duration_sec"):
            clip_duration = clip_meta["duration_sec"]

        # Calculate kept segments
        keep_segments = build_keep_segments(
            h_transcript.get("words", []),
            clip_duration=clip_duration,
            min_silence_sec=min_silence_sec,
            padding_ms=padding_ms,
            fillers=fillers,
        )

        cut_transcript = remap_transcript_to_cuts(h_transcript, keep_segments)

        orig_dur = clip_duration
        new_dur = cut_transcript["duration_sec"]
        saved_dur = max(0.0, orig_dur - new_dur)
        pct_saved = (saved_dur / orig_dur * 100) if orig_dur > 0 else 0
        logger.info(
            f"  [cutter] Kept {len(keep_segments)} speech segments | "
            f"Duration: {orig_dur:.1f}s → {new_dur:.1f}s ({saved_dur:.1f}s cut, -{pct_saved:.0f}%)"
        )

        # Cache remapped transcript
        cut_transcript_path = ctx.root / "cache" / "transcripts" / f"{stem}_rough_cut.json"
        cut_transcript_path.parent.mkdir(parents=True, exist_ok=True)
        cut_transcript_path.write_text(
            json.dumps(cut_transcript, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        if clip_meta:
            src_video_path = Path(clip_meta["path"])
            has_audio = clip_meta.get("has_audio", True)
            rough_cut_video = ctx.output_dir / f"{stem}_rough_cut.mp4"

            # Render cut video
            render_rough_cut(
                video_path=src_video_path,
                keep_segments=keep_segments,
                output_path=rough_cut_video,
                encoder=ffmpeg_cfg.get("video_encoder", "h264_nvenc"),
                fallback_encoder=ffmpeg_cfg.get("fallback_encoder", "libx264"),
                has_audio=has_audio,
            )

            # English SRT
            en_srt = ctx.output_dir / f"{stem}_rough_cut_en.srt"
            generate_srt(
                cut_transcript,
                en_srt,
                use_hinglish=False,
                words_per_line=int(cap_cfg.get("words_per_line", 3)),
                max_lines=int(cap_cfg.get("max_lines", 2)),
            )

            # Hinglish SRT
            hi_srt = ctx.output_dir / f"{stem}_rough_cut_hinglish.srt"
            generate_srt(
                cut_transcript,
                hi_srt,
                use_hinglish=True,
                words_per_line=int(cap_cfg.get("words_per_line", 3)),
                max_lines=int(cap_cfg.get("max_lines", 2)),
            )

            # Re-timed Styled ASS
            ass_out = ctx.output_dir / f"{stem}_rough_cut_word_highlight.ass"
            generate_ass(
                cut_transcript,
                ass_out,
                font=cap_cfg.get("font", "Montserrat ExtraBold"),
                size=int(cap_cfg.get("size", 72)),
                highlight_color=cap_cfg.get("highlight_color", "&H0000D4FF"),
                text_color=cap_cfg.get("text_color", "&H00FFFFFF"),
                outline_color=cap_cfg.get("outline_color", "&H00000000"),
                outline_size=int(cap_cfg.get("outline_size", 4)),
                shadow=int(cap_cfg.get("shadow", 2)),
                position=cap_cfg.get("position", "lower_third"),
                words_per_line=int(cap_cfg.get("words_per_line", 3)),
                max_lines=int(cap_cfg.get("max_lines", 2)),
                style=cap_cfg.get("style", "word_popup"),
                use_hinglish=hinglish_enabled,
            )

            # Burn re-timed captions into rough cut video
            if burn:
                captioned_rough_cut = ctx.output_dir / f"{stem}_rough_cut_captioned.mp4"
                burn_captions(
                    rough_cut_video,
                    ass_out,
                    captioned_rough_cut,
                    encoder=ffmpeg_cfg.get("video_encoder", "h264_nvenc"),
                    fallback_encoder=ffmpeg_cfg.get("fallback_encoder", "libx264"),
                )

    # ── 5. Convenience copies for single-clip projects ─────────────────────────
    logger.info("─── Step 5/5: Finalise ─────────────────────────────────────────")
    if len(transcripts) == 1:
        stem = Path(next(iter(transcripts))).stem
        for src_name, dst_name in [
            (f"{stem}_rough_cut.mp4",               "rough_cut.mp4"),
            (f"{stem}_rough_cut_en.srt",            "rough_cut_en.srt"),
            (f"{stem}_rough_cut_hinglish.srt",      "rough_cut_hinglish.srt"),
            (f"{stem}_rough_cut_word_highlight.ass", "rough_cut_hinglish.ass"),
            (f"{stem}_rough_cut_captioned.mp4",     "rough_cut_captioned.mp4"),
        ]:
            src = ctx.output_dir / src_name
            dst = ctx.output_dir / dst_name
            if src.exists():
                shutil.copy2(src, dst)
                logger.debug(f"[main] Copied {src_name} → {dst_name}")

    logger.info(f"✅  Rough Cut complete.  Outputs in: {ctx.output_dir}")
    outputs = sorted(ctx.output_dir.glob("*"))
    for f in outputs:
        if f.name != "log.txt":
            logger.info(f"   {f.name}")


# ─────────────────────────── entry point ─────────────────────────────────────

def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = _build_parser()
    args = parser.parse_args()

    # Load .env before anything that reads env vars
    load_env(Path(args.project_dir))

    # Config
    cfg = load_config(_CONFIG_PATH) if _CONFIG_PATH.exists() else {}

    # Project context (creates cache/ and output/ automatically)
    ctx = ProjectContext(Path(args.project_dir))

    # Logging (file + console)
    log_level = logging.DEBUG if args.debug else logging.INFO
    setup_logging(log_file=ctx.log_path, level=log_level)

    logger.info(f"StyleCut  |  project: {ctx.root.name}")

    # Validate project directory
    errors = ctx.validate()
    if errors:
        for e in errors:
            logger.error(e)
        sys.exit(1)

    # Client preset
    client: dict = {}
    if args.client:
        client = _load_client(args.client)

    # Dispatch to the requested pipeline
    if args.rough_cut:
        _run_rough_cut(ctx, cfg, client, args)
    elif args.captions_only:
        _run_captions(ctx, cfg, client, args)
    else:
        logger.info(
            "No pipeline mode selected.\n"
            "  Phase 1 flag:  --captions-only\n"
            "  Phase 2 flag:  --rough-cut\n"
        )
        parser.print_help()


if __name__ == "__main__":
    main()
