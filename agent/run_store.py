"""Per-run workspace on disk — the single place job data lives during a run.

Hub-and-spoke rule: tools never hand job lists to the LLM to pass along.
Each tool reads its inputs from the run store, writes its outputs back, and
returns a short text summary. That keeps the orchestrator's context small and
makes it impossible for the model to alter URLs or scores in transit.

Layout (one directory per run, gitignored under ``.data/``)::

    .data/runs/<run_id>/
        cvs/<name>.md          CVs in Markdown (PDFs converted on the way in)
        candidates.jsonl       validated, deduplicated job postings
        scored.jsonl           jobs at or above the score threshold
        discarded.jsonl        jobs below the threshold (score + reason kept)
"""
import json
from pathlib import Path

DEFAULT_ROOT = Path(".data/runs")

# Canonical file names — tools refer to these constants, never to raw strings.
CANDIDATES = "candidates.jsonl"
SCORED = "scored.jsonl"
DISCARDED = "discarded.jsonl"


class RunStore:
    """Read/write helpers scoped to one run directory."""

    def __init__(self, run_id: str, root: Path = DEFAULT_ROOT) -> None:
        self.run_id = run_id
        self.dir = root / run_id
        self.cv_dir = self.dir / "cvs"
        self.cv_dir.mkdir(parents=True, exist_ok=True)

    # ── CVs ──────────────────────────────────────────────────────────────────

    def save_cv(self, name: str, content: str) -> Path:
        """Store a Markdown CV under ``cvs/<name>.md`` and return its path."""
        path = self.cv_dir / f"{name}.md"
        path.write_text(content, encoding="utf-8")
        return path

    def load_cvs(self) -> list[dict]:
        """Return every stored CV as ``{"name", "content", "path"}`` (sorted by name)."""
        return [
            {"name": p.stem, "content": p.read_text(encoding="utf-8"), "path": str(p)}
            for p in sorted(self.cv_dir.glob("*.md"))
        ]

    # ── JSONL tables ─────────────────────────────────────────────────────────

    def read_jsonl(self, name: str) -> list[dict]:
        """Read a JSONL table; a missing file is an empty table."""
        path = self.dir / name
        if not path.exists():
            return []
        with path.open(encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def write_jsonl(self, name: str, rows: list[dict]) -> None:
        """Overwrite a JSONL table with ``rows``."""
        lines = [json.dumps(row, ensure_ascii=False) for row in rows]
        (self.dir / name).write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
