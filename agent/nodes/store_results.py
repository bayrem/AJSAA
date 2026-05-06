"""Persist scored jobs to configured storage provider."""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from agent.state import AgentState

logger = logging.getLogger(__name__)

_META_PATH = Path(".data/meta.json")


def _load_meta() -> dict:
    if _META_PATH.exists():
        try:
            return json.loads(_META_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_meta(data: dict) -> None:
    _META_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = _load_meta()
    existing.update(data)
    _META_PATH.write_text(json.dumps(existing, indent=2), encoding="utf-8")


def run(state: AgentState) -> AgentState:
    errors = list(state.get("errors", []))
    run_log = list(state.get("run_log", []))

    scored_jobs = state.get("scored_jobs", [])
    if not scored_jobs:
        run_log.append("No scored jobs to store")
        return {**state, "stored_count": 0, "errors": errors, "run_log": run_log}

    cfg = state["config"]

    for job in scored_jobs:
        job.setdefault("date_found", state.get("timestamp", datetime.now(timezone.utc).isoformat()))
        job.setdefault("status", "new")

    try:
        from providers.storage.factory import build_storage
        storage = build_storage(cfg["storage"])
        new_count = storage.save(scored_jobs)
        sheet_url = getattr(storage, "last_sheet_url", None)

        # Persist sheet_url so test_notification and future runs can reference it
        meta: dict = {}
        if sheet_url:
            meta["sheet_url"] = sheet_url
        meta["last_run"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        meta["last_stored"] = new_count
        _save_meta(meta)

        run_log.append(f"Stored {new_count} new jobs (provider: {cfg['storage']['provider']})")
        logger.info("Stored %d new jobs", new_count)
    except Exception as e:
        errors.append(f"Storage failed: {e}")
        logger.error("Storage failed: %s", e)
        new_count = 0
        sheet_url = None

    # Fallback: if no sheet_url from this run, load last known from meta
    if not sheet_url:
        sheet_url = _load_meta().get("sheet_url")

    return {
        **state,
        "stored_count": new_count,
        "sheet_url": sheet_url,
        "errors": errors,
        "run_log": run_log,
    }
