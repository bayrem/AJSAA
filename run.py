"""AJSAA — main entry point.

Boots the LangGraph pipeline, streams node updates into a Rich dashboard, and
writes the after-action report when the run finishes.

Usage::

    python run.py                  # use config.yaml
    python run.py --config foo.yaml
    python run.py --dry-run        # force storage.provider=local

Exit code is 1 if any node recorded an error, 0 otherwise.
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv
from rich.live import Live
from rich.table import Table
from rich.text import Text

# Load .env BEFORE any other imports so env-driven module-level constants
# (e.g. SMTP host) pick up overrides.
load_dotenv()


# Fixed pipeline order. The dashboard shows nodes in this order and the
# after-action report uses it to render the per-node timing table.
NODE_ORDER = [
    "load_context",
    "convert_cvs",
    "generate_queries",
    "search_jobs",
    "search_companies",
    "analyze_jobs",
    "store_results",
    "send_notifications",
]


# ── KPI extraction (dispatch by node name) ───────────────────────────────────

def _analyze_kpis(updates: dict) -> tuple[str, str]:
    """KPI extractor for the analyze_jobs node — counts by recommendation."""
    scored = updates.get("scored_jobs", [])
    apply_n = sum(1 for j in scored if j.get("recommendation") == "APPLY")
    consider_n = sum(1 for j in scored if j.get("recommendation") == "CONSIDER")
    return (f"Passed: {len(scored)}", f"APPLY: {apply_n}  CONSIDER: {consider_n}")


# Each entry returns ``(kpi1_label, kpi2_label)`` — the two-column summary
# shown next to the node name in the live dashboard. Replaces an
# if/elif chain. Most entries are inline lambdas because they're one-liners;
# ``analyze_jobs`` is its own function because the logic doesn't fit on one line.
_KPI_EXTRACTORS: dict[str, Callable[[dict], tuple[str, str]]] = {
    "load_context": lambda u: (
        f"CVs: {len(u.get('cvs', []))}",
        f"Companies: {len(u.get('companies', []))}",
    ),
    "convert_cvs": lambda u: (f"Profiles: {len(u.get('cvs', []))}", ""),
    "generate_queries": lambda u: (f"Queries: {len(u.get('queries', []))}", ""),
    "search_jobs": lambda u: (f"Found: {len(u.get('raw_jobs', []))}", ""),
    "search_companies": lambda u: (f"Found: {len(u.get('raw_jobs', []))}", ""),
    "analyze_jobs": _analyze_kpis,
    "store_results": lambda u: (f"New: {u.get('stored_count', 0)}", ""),
    "send_notifications": lambda u: (
        "Sent: yes" if u.get("notification_sent", False) else "Sent: no",
        "",
    ),
}


def _extract_kpis(node_name: str, updates: dict) -> tuple[str, str]:
    """Return the (kpi1, kpi2) labels for ``node_name``, or em-dashes if unknown."""
    extractor = _KPI_EXTRACTORS.get(node_name)
    return extractor(updates) if extractor else ("—", "—")


# ── Dashboard rendering ──────────────────────────────────────────────────────

# Status icon palette used by the dashboard. ``waiting`` is the only state
# where a column is shown as empty (the others render coloured symbols).
_STATUS_SYMBOLS = {
    "done": ("✓", "green"),
    "error": ("✗", "red"),
    "running": ("⟳", "yellow"),
    "waiting": ("○", "dim"),
}


def _make_dashboard(
    statuses: dict,
    kpis: dict,
    timings: dict,
    run_id: str,
    ts: str,
) -> Table:
    """Build the live Rich table shown during the run."""
    table = Table(title=f"AJSAA  {run_id}  •  {ts}", expand=True, show_lines=False)
    table.add_column("NODE", style="bold", min_width=20)
    table.add_column("KPI 1", min_width=22)
    table.add_column("KPI 2", min_width=24)
    table.add_column("STATUS", justify="center", min_width=8)
    table.add_column("TIME", justify="right", min_width=7)

    for node in NODE_ORDER:
        status = statuses.get(node, "waiting")
        kpi1, kpi2 = kpis.get(node, ("—", "—"))
        elapsed = timings.get(node)
        time_str = f"{elapsed:.1f}s" if elapsed is not None else "—"
        glyph, colour = _STATUS_SYMBOLS.get(status, _STATUS_SYMBOLS["waiting"])
        table.add_row(node, kpi1, kpi2, Text(glyph, style=colour), time_str)

    return table


# ── Logging setup ────────────────────────────────────────────────────────────

def _setup_logging(cfg: dict, run_id: str) -> None:
    """Configure stdout + file logging based on config.yaml's ``logging`` block.

    Supports three rotation modes:
      - ``none``    — single growing file at ``log.file`` (default).
      - ``daily``   — rotate at midnight, keep ``retention`` backups.
      - ``per_run`` — write a fresh file per run, retain last ``retention``.
    """
    log_cfg = cfg.get("logging", {})
    level = getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO)
    log_file = log_cfg.get("file", "logs/job_search.log")
    rotation = log_cfg.get("rotation", "none")
    retention = int(log_cfg.get("retention", 7))

    Path(log_file).parent.mkdir(parents=True, exist_ok=True)

    if rotation == "daily":
        file_handler: logging.Handler = logging.handlers.TimedRotatingFileHandler(
            log_file, when="midnight", backupCount=retention, encoding="utf-8"
        )
    elif rotation == "per_run":
        runs_dir = Path("logs/runs")
        runs_dir.mkdir(parents=True, exist_ok=True)
        ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_log_path = runs_dir / f"run_{ts_str}.log"
        file_handler = logging.FileHandler(run_log_path, encoding="utf-8")
        # Prune old per-run logs ourselves — TimedRotatingFileHandler can't
        # do this because we're writing one-shot files, not rotating one.
        all_logs = sorted(runs_dir.glob("run_*.log"), key=lambda p: p.stat().st_mtime)
        for old in all_logs[:-retention]:
            old.unlink(missing_ok=True)
    else:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        handlers=[logging.StreamHandler(sys.stdout), file_handler],
    )


# ── Config / state bootstrap ─────────────────────────────────────────────────

def _load_config(path: str = "config.yaml") -> dict:
    """Read config.yaml from disk and return the parsed dict."""
    import yaml  # imported lazily so non-config code paths don't pay for it
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _build_initial_state(cfg: dict, run_id: str, ts: str) -> dict:
    """Return the starting :class:`AgentState` for a new run.

    Every field declared on ``AgentState`` is set to its zero value here so
    nodes can use ``state["x"]`` without ``KeyError`` defensiveness.
    """
    return {
        "run_id": run_id,
        "timestamp": ts,
        "config": cfg,
        "cvs": [],
        "raw_queries": [],
        "companies": [],
        "company_hints": {},
        "pdf_paths": [],
        "queries": [],
        "raw_jobs": [],
        "scored_jobs": [],
        "stored_count": 0,
        "sheet_url": None,
        "notification_sent": False,
        "errors": [],
        "run_log": [],
    }


# ── Pipeline execution ───────────────────────────────────────────────────────

def _run_pipeline(graph, initial_state: dict, run_id: str, ts: str) -> tuple[dict, dict, float]:
    """Stream the graph with a live dashboard, returning final state + timings.

    Returns:
        ``(final_state, node_timings, total_duration_seconds)``.
    """
    statuses: dict[str, str] = {n: "waiting" for n in NODE_ORDER}
    kpis_display: dict[str, tuple[str, str]] = {n: ("—", "—") for n in NODE_ORDER}
    node_timings: dict[str, float] = {}
    run_start = time.time()
    node_start = time.time()
    final_state: dict = dict(initial_state)

    with Live(
        _make_dashboard(statuses, kpis_display, node_timings, run_id, ts),
        refresh_per_second=4,
        transient=True,
    ) as live:
        for event in graph.stream(initial_state, stream_mode="updates"):
            for node_name, updates in event.items():
                # Per-node timing — measured between updates so it accounts
                # for whatever async work the node did before yielding.
                elapsed = time.time() - node_start
                node_start = time.time()

                if node_name in NODE_ORDER:
                    # If this node added new errors, mark it as failed even
                    # if the function completed — partial failures still
                    # set the error state on the dashboard.
                    prev_err_count = len(final_state.get("errors", []))
                    new_err_count = len(updates.get("errors", []))
                    statuses[node_name] = "error" if new_err_count > prev_err_count else "done"

                    kpis_display[node_name] = _extract_kpis(node_name, updates)
                    node_timings[node_name] = elapsed

                    # Pre-mark the next node as "running" so the user sees
                    # immediate feedback once a node finishes.
                    next_idx = NODE_ORDER.index(node_name) + 1
                    if next_idx < len(NODE_ORDER):
                        statuses[NODE_ORDER[next_idx]] = "running"

                final_state.update(updates)
                live.update(_make_dashboard(statuses, kpis_display, node_timings, run_id, ts))

    return final_state, node_timings, time.time() - run_start


def _write_reports(
    final_state: dict,
    node_timings: dict,
    run_duration: float,
    run_id: str,
    ts: str,
    logger: logging.Logger,
) -> None:
    """Generate the after-action HTML report and update the run index/json.

    Failures here are non-fatal — the pipeline already ran successfully, so
    a broken report writer should not change the exit code.
    """
    try:
        from scripts.report import append_runs_json, generate_run_report, update_index
        stats = {
            "queries": len(final_state.get("queries", [])),
            "found": len(final_state.get("raw_jobs", [])),
            "passed": len(final_state.get("scored_jobs", [])),
            "new_saved": final_state.get("stored_count", 0),
            "errors": len(final_state.get("errors", [])),
        }
        report_path = generate_run_report(final_state, run_duration, node_timings)
        update_index(run_id, ts, run_duration, stats)
        append_runs_json(run_id, ts, run_duration, stats)
        logger.info("After-action report: %s", report_path)
    except Exception as e:
        logger.warning("After-action report failed: %s", e)


# ── Main entrypoint ──────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="AJSAA — Autonomous Job Search AI Agent")
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
    parser.add_argument("--dry-run", action="store_true", help="Score jobs without writing to storage")
    args = parser.parse_args()

    cfg = _load_config(args.config)
    run_id = str(uuid.uuid4())[:8]
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    _setup_logging(cfg, run_id)
    logger = logging.getLogger("ajsaa")

    if args.dry_run:
        # Force local storage so we never accidentally write to the user's
        # Google Sheet during a dry run.
        cfg["storage"]["provider"] = "local"
        logger.info("Dry-run mode — storage writes disabled")

    logger.info("=" * 60)
    logger.info("AJSAA run starting  [run_id=%s]", run_id)

    # Lazy import — the graph builds the full node import chain
    from agent.graph import build_graph
    graph = build_graph()
    initial_state = _build_initial_state(cfg, run_id, ts)

    final_state, node_timings, run_duration = _run_pipeline(graph, initial_state, run_id, ts)

    _write_reports(final_state, node_timings, run_duration, run_id, ts, logger)

    logger.info("Run complete — %d new jobs stored", final_state.get("stored_count", 0))
    if final_state.get("errors"):
        for err in final_state["errors"]:
            logger.error("  ERROR: %s", err)
    logger.info("=" * 60)

    if final_state.get("errors"):
        sys.exit(1)


if __name__ == "__main__":
    main()
