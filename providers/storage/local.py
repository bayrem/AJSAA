"""Local JSON storage — source of truth for all runs."""
import json
import logging
from pathlib import Path

from providers.storage.base import BaseStorageProvider

logger = logging.getLogger(__name__)

SCHEMA_FIELDS = [
    "job_id", "date_found", "title", "company", "location",
    "url", "best_cv", "score", "summary", "status", "description",
]


class LocalJSONProvider(BaseStorageProvider):
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load_all(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

    def save(self, jobs: list[dict]) -> int:
        existing = self.load_all()
        existing_ids = {j["job_id"] for j in existing if j.get("job_id")}

        new_jobs = [j for j in jobs if j.get("job_id") not in existing_ids]
        if new_jobs:
            all_jobs = existing + new_jobs
            self.path.write_text(
                json.dumps(all_jobs, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.info("Saved %d new jobs to %s", len(new_jobs), self.path)
        return len(new_jobs)
