"""Project directory context — all paths in one place."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ProjectContext:
    """Resolves and manages all paths for a single StyleCut project.

    Guaranteed structure after construction::

        <root>/
        ├── footage/      (must be created by the user)
        ├── cache/        (auto-created)
        │   ├── ingest/
        │   ├── transcripts/
        │   └── llm/
        ├── output/       (auto-created)
        │   └── log.txt
        └── brand/        (optional)
    """

    root: Path

    footage_dir: Path = field(init=False)
    cache_dir: Path = field(init=False)
    output_dir: Path = field(init=False)
    brand_dir: Path = field(init=False)

    def __post_init__(self) -> None:
        self.root = Path(self.root).resolve()
        self.footage_dir = self.root / "footage"
        self.cache_dir = self.root / "cache"
        self.output_dir = self.root / "output"
        self.brand_dir = self.root / "brand"
        # Always ensure writable dirs exist
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ── cache sub-dirs ─────────────────────────────────────────────

    @property
    def ingest_cache_dir(self) -> Path:
        return self.cache_dir / "ingest"

    @property
    def transcript_cache_dir(self) -> Path:
        return self.cache_dir / "transcripts"

    @property
    def llm_cache_dir(self) -> Path:
        return self.cache_dir / "llm"

    # ── well-known files ───────────────────────────────────────────

    @property
    def script_path(self) -> Path:
        return self.root / "script.txt"

    @property
    def brief_path(self) -> Path:
        return self.root / "brief.txt"

    @property
    def log_path(self) -> Path:
        return self.output_dir / "log.txt"

    @property
    def edit_plan_path(self) -> Path:
        return self.output_dir / "edit_plan.json"

    # ── validation ─────────────────────────────────────────────────

    def validate(self) -> list[str]:
        """Return a list of error strings.  Empty list → project is OK."""
        errors: list[str] = []
        if not self.root.exists():
            errors.append(f"Project directory does not exist: {self.root}")
        if not self.footage_dir.exists():
            errors.append(
                f"Missing footage directory: {self.footage_dir}\n"
                "  Create it and add your video clips."
            )
        return errors

    def __str__(self) -> str:  # noqa: D105
        return str(self.root)
