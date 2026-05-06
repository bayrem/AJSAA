"""Google Drive storage — syncs .data/jobs.json as a spreadsheet via MCP.

This provider is designed to be called by the Claude Code agent, which has
the Google Drive MCP connected. The agent reads jobs from local storage and
creates/updates the Drive spreadsheet using MCP tool calls.

For direct Python use (non-Claude-Code context), falls back to local-only.
"""
import csv
import io
import logging

from providers.storage.local import LocalJSONProvider

logger = logging.getLogger(__name__)

SHEET_HEADERS = [
    "date_found", "job_id", "title", "company", "location",
    "url", "best_cv", "score", "summary", "status",
]


class GoogleDriveProvider(LocalJSONProvider):
    """
    Extends LocalJSONProvider — always writes to local first (source of truth),
    then exposes a CSV representation for MCP-based Drive sync.

    The actual Drive upload is handled by the Claude agent (CLAUDE.md instructions)
    via the Google Drive MCP after each pipeline run.
    """

    def __init__(self, cfg: dict):
        path = cfg.get("local_path", ".data/jobs.json")
        super().__init__(path)
        self.sheet_name = cfg.get("sheet_name", "AJSAA Job Search")
        self.last_sheet_url = None

    def to_csv(self) -> str:
        jobs = self.load_all()
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=SHEET_HEADERS, extrasaction="ignore")
        writer.writeheader()
        for job in sorted(jobs, key=lambda j: j.get("score", 0), reverse=True):
            writer.writerow({h: job.get(h, "") for h in SHEET_HEADERS})
        return buf.getvalue()
