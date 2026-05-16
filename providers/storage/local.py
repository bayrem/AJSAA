"""Local JSON storage — source of truth for every storage provider.

Cloud providers extend this class so that every run produces a canonical
local file regardless of the cloud sync state. The local file's path
defaults to ``.data/jobs.json`` (gitignored) and is configurable.
"""
import json
import logging
from pathlib import Path

from providers.storage.base import BaseStorageProvider

logger = logging.getLogger(__name__)


# Canonical column order — used by spreadsheet exports (google_drive.py).
# Order matters because the headers in the sheet need to be stable across runs.
SCHEMA_FIELDS = [
    "job_id", "date_found", "title", "company", "location",
    "url", "best_cv", "score", "summary", "status", "description",
]


class LocalJSONProvider(BaseStorageProvider):
    """Append-only JSON file with content-addressed dedup by ``job_id``."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        # Make sure the parent directory exists so the first .save() doesn't
        # blow up on a missing folder.
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load_all(self) -> list[dict]:
        """Return every stored job, or ``[]`` if the file is missing or corrupt."""
        if not self.path.exists():
            return []
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # Corrupt file shouldn't crash the run. The next save() will
            # rewrite it from scratch (effectively losing the old data,
            # which is acceptable because new jobs are also being added now).
            return []

    def save(self, jobs: list[dict]) -> int:
        """Merge ``jobs`` into the file, deduping by ``job_id``."""
        existing = self.load_all()
        existing_ids = {j["job_id"] for j in existing if j.get("job_id")}

        new_jobs = [j for j in jobs if j.get("job_id") not in existing_ids]
        if new_jobs:
            all_jobs = existing + new_jobs
            # ``ensure_ascii=False`` so accented characters (Telétravail,
            # Île-de-France) round-trip cleanly.
            self.path.write_text(
                json.dumps(all_jobs, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.info("Saved %d new jobs to %s", len(new_jobs), self.path)
        return len(new_jobs)
