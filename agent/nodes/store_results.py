"""Persist scored jobs to the configured storage backend.

Responsibilities:
  - Stamp each job with ``date_found`` and a default ``status`` of ``"new"``
    before handing it to the storage provider (so every downstream consumer
    sees a uniform schema regardless of source).
  - Invoke the storage provider via the factory.
  - Capture and persist ``sheet_url`` to ``.data/meta.json`` so notifications
    sent on later runs can still link to the most recent sheet.
"""
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.state import AgentState
from providers.utils import JsonCache

logger = logging.getLogger(__name__)


# Cross-run metadata: stores the latest sheet_url and last run timestamp so
# test_notification.py and the notification node can reference them even when
# the current run produced none.
_META_CACHE = JsonCache(Path(".data/meta.json"))


def _update_meta(updates: dict) -> None:
    """Merge ``updates`` into the persisted meta dict."""
    existing = _META_CACHE.load()
    if not isinstance(existing, dict):
        existing = {}
    existing.update(updates)
    _META_CACHE.save(existing)


def run(state: AgentState) -> AgentState:
    errors = list(state.get("errors", []))
    run_log = list(state.get("run_log", []))

    scored_jobs = state.get("scored_jobs", [])
    if not scored_jobs:
        run_log.append("No scored jobs to store")
        return {**state, "stored_count": 0, "errors": errors, "run_log": run_log}

    cfg = state["config"]

    # Ensure every job has the storage-required fields. Using setdefault means
    # we never overwrite values the scorer already produced.
    for job in scored_jobs:
        job.setdefault("date_found", state.get("timestamp", datetime.now(timezone.utc).isoformat()))
        job.setdefault("status", "new")

    new_count = 0
    sheet_url: str | None = None

    try:
        from providers.storage.factory import build_storage
        storage = build_storage(cfg["storage"])
        new_count = storage.save(scored_jobs)

        # Google Sheets provider exposes the most recently used spreadsheet URL
        # via this attribute; other providers don't and that's fine.
        sheet_url = getattr(storage, "last_sheet_url", None)

        meta_updates: dict[str, Any] = {
            "last_run": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "last_stored": new_count,
        }
        if sheet_url:
            meta_updates["sheet_url"] = sheet_url
        _update_meta(meta_updates)

        run_log.append(f"Stored {new_count} new jobs (provider: {cfg['storage']['provider']})")
        logger.info("Stored %d new jobs", new_count)
    except Exception as e:
        errors.append(f"Storage failed: {e}")
        logger.error("Storage failed: %s", e)

    # Fall back to the last sheet_url we ever saw so notifications still link
    # to *some* sheet even when the current run failed to produce one.
    if not sheet_url:
        meta = _META_CACHE.load()
        sheet_url = meta.get("sheet_url") if isinstance(meta, dict) else None

    return {
        **state,
        "stored_count": new_count,
        "sheet_url": sheet_url,
        "errors": errors,
        "run_log": run_log,
    }
