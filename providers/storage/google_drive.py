"""Google Drive storage — local JSON + spreadsheet sync via MCP.

This provider keeps the same JSON-on-disk source-of-truth as the local
backend but additionally exposes a CSV representation so the Claude Code
agent can sync the data to Google Sheets via the Google Drive MCP. The
actual upload happens outside this class — see the project's CLAUDE.md
for the wiring.

For non-Claude-Code use, this provider degrades gracefully to local-only
behaviour because it inherits from :class:`LocalJSONProvider`.
"""
import csv
import io
import logging

from providers.storage.local import LocalJSONProvider

logger = logging.getLogger(__name__)


# Order chosen for readability in the spreadsheet — most-glanceable columns
# first. ``description`` is omitted because it's too long to be useful in a
# tabular view (~1000 chars per row); reviewers should click the URL instead.
SHEET_HEADERS = [
    "date_found", "job_id", "title", "company", "location",
    "url", "best_cv", "score", "summary", "status",
]


class GoogleDriveProvider(LocalJSONProvider):
    """Local JSON + CSV export for downstream MCP-based sheet sync."""

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg.get("local_path", ".data/jobs.json"))
        self.sheet_name = cfg.get("sheet_name", "AJSAA Job Search")
        # Populated by the Claude agent after a successful sheet sync so
        # store_results can stamp it into .data/meta.json for notifications.
        self.last_sheet_url: str | None = None

    def to_csv(self) -> str:
        """Render the stored jobs as CSV with the canonical column order.

        Sorted by score descending so the spreadsheet's top rows are always
        the best matches.
        """
        jobs = self.load_all()
        buf = io.StringIO()
        # ``extrasaction="ignore"`` lets us skip any extra keys jobs may carry
        # (e.g. score_breakdown) without raising — schema may evolve over time.
        writer = csv.DictWriter(buf, fieldnames=SHEET_HEADERS, extrasaction="ignore")
        writer.writeheader()
        for job in sorted(jobs, key=lambda j: j.get("score", 0), reverse=True):
            writer.writerow({h: job.get(h, "") for h in SHEET_HEADERS})
        return buf.getvalue()
