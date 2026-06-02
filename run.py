"""AJSAA — main entry point.

Boots the LangGraph pipeline, streams node updates into a Rich dashboard, and
writes the after-action report when the run finishes.

Usage::

    python run.py                       # merge config/ folder (preferred)
    python run.py --config foo.yaml     # explicit single-file override
    python run.py --dry-run             # force storage.provider=local
    python run.py --port 9000           # override live monitor port
    python run.py --no-monitor          # disable the live HTTP monitor

Exit code is 1 if any node recorded an error, 0 otherwise.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Uncomment if not using a secrets manager (e.g. Infisical):
# from dotenv import load_dotenv
# load_dotenv()
from rich.live import Live

from monitoring.logging_setup import setup_logging
from monitoring.monitoring_core.constants import NODE_ORDER
from monitoring.monitoring_core.token_summary import format_token_summary
from monitoring.tui_monitoring.dashboard import extract_kpis, make_live_view


def _merge_dicts(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base*, returning a new dict.

    Nested dicts are merged rather than replaced so a partial override file
    does not wipe out keys it doesn't mention.
    """
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _merge_dicts(result[key], val)
        else:
            result[key] = val
    return result


def _load_config(path: str | None = None) -> dict:
    """Load configuration by merging the config/ folder layout.

    When *path* is given (explicit ``--config`` flag) it is loaded as a single
    file, bypassing the new layout entirely.  This preserves backwards compat
    for one-off overrides and test fixtures.

    Default behaviour (no *path*):
      1. Merge config/config.yaml, config/search_config.yaml, config/score_config.yaml.
      2. If the legacy root config.yaml is still present, emit a deprecation
         warning — but do NOT load it (the config/ folder takes precedence).
    """

    import yaml  # imported lazily so non-config code paths don't pay for it

    config_dir = Path("config")

    if path is not None:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)

    merged: dict = {}
    for fname in ("config.yaml", "search_config.yaml", "score_config.yaml"):
        file_path = config_dir / fname
        with open(file_path, encoding="utf-8") as f:
            partial = yaml.safe_load(f) or {}
        merged = _merge_dicts(merged, partial)

    return merged



def _build_initial_state(cfg: dict, run_id: str, ts: str) -> dict:
    return {
        "run_id": run_id,
        "timestamp": ts,
        "run_start_time": time.time(),  # Unix timestamp — used by live dashboard duration counter
        "config": cfg,
        "cvs": [],
        "raw_queries": [],
        "companies": [],
        "company_hints": {},
        "pdf_paths": [],
        "queries": [],
        "raw_jobs": [],
        "scored_jobs": [],
        "discarded_jobs": [],
        "stored_count": 0,
        "sheet_url": None,
        "notification_sent": False,
        "errors": [],
        "run_log": [],
        "token_usage": {},
    }


def _run_pipeline(graph, initial_state: dict, run_id: str, ts: str) -> tuple[dict, dict, float]:
    """Stream the graph with a live TUI, returning (final_state, node_timings, duration)."""
    statuses: dict[str, str] = {n: "waiting" for n in NODE_ORDER}
    kpis_display: dict[str, tuple[str, str]] = {n: ("—", "—") for n in NODE_ORDER}
    node_timings: dict[str, float] = {}
    run_start = time.time()
    node_start = time.time()
    final_state: dict = dict(initial_state)

    from providers.llm import usage_tracker

    with Live(
        make_live_view(statuses, kpis_display, node_timings, run_id, ts, usage_tracker.snapshot()),
        refresh_per_second=4,
        transient=True,
    ) as live:
        for event in graph.stream(initial_state, stream_mode="updates"):
            for node_name, updates in event.items():
                elapsed = time.time() - node_start
                node_start = time.time()

                if node_name in NODE_ORDER:
                    prev_err_count = len(final_state.get("errors", []))
                    new_err_count = len(updates.get("errors", []))
                    statuses[node_name] = "error" if new_err_count > prev_err_count else "done"

                    kpis_display[node_name] = extract_kpis(node_name, updates)
                    node_timings[node_name] = elapsed

                    next_idx = NODE_ORDER.index(node_name) + 1
                    if next_idx < len(NODE_ORDER):
                        statuses[NODE_ORDER[next_idx]] = "running"

                final_state.update(updates)
                live.update(
                    make_live_view(
                        statuses, kpis_display, node_timings, run_id, ts,
                        usage_tracker.snapshot(),
                    )
                )

    return final_state, node_timings, time.time() - run_start


def _write_reports(
    final_state: dict,
    node_timings: dict,
    run_duration: float,
    run_id: str,
    ts: str,
    logger: logging.Logger,
) -> None:
    try:
        from monitoring.web_monitoring.report import append_runs_json, generate_run_report, update_index
        token_usage = final_state.get("token_usage") or {}
        grand_total = token_usage.get("grand_total") or {}
        stats = {
            "queries": len(final_state.get("queries", [])),
            "found": len(final_state.get("raw_jobs", [])),
            "passed": len(final_state.get("scored_jobs", [])),
            "new_saved": final_state.get("stored_count", 0),
            "errors": len(final_state.get("errors", [])),
            "cost_usd": float(grand_total.get("cost_usd", 0.0) or 0.0),
            "tokens_total": sum(
                int(grand_total.get(k) or 0)
                for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
            ),
        }
        report_path = generate_run_report(final_state, run_duration, node_timings)
        append_runs_json(run_id, ts, run_duration, stats)
        update_index(run_id, ts, run_duration, stats)
        logger.info("After-action report: %s", report_path)
    except Exception as e:
        logger.warning("After-action report failed: %s", e)


def _push_final_state(monitor, final_state: dict, node_timings: dict) -> None:
    status = "failed" if final_state.get("errors") else "complete"
    monitor.update_state({
        "run_id": final_state.get("run_id", "unknown"),
        "timestamp": final_state.get("timestamp", ""),
        "status": status,
        "current_node": None,
        "node_status": {n: "complete" for n in NODE_ORDER if n in node_timings},
        "node_timings": dict(node_timings),
        "kpis": {
            "raw_jobs": len(final_state.get("raw_jobs", [])),
            "scored_jobs": len(final_state.get("scored_jobs", [])),
            "stored_count": final_state.get("stored_count", 0),
        },
        "token_usage": final_state.get("token_usage", {}),
        "errors": list(final_state.get("errors", [])),
        "scored_jobs": list(final_state.get("scored_jobs", [])),
    })


def _valid_port(raw: str) -> int:
    try:
        n = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"port must be an integer: {raw!r}") from exc
    if not (1024 <= n <= 65535):
        raise argparse.ArgumentTypeError(f"port must be in 1024-65535, got {n}")
    return n


def main() -> None:
    parser = argparse.ArgumentParser(description="AJSAA — Autonomous Job Search AI Agent")
    parser.add_argument("--config", default=None, help="Path to a single config file. Omit to use the config/ folder layout.")
    parser.add_argument("--dry-run", action="store_true", help="Score jobs without writing to storage")
    parser.add_argument("--port", type=_valid_port, default=8765, help="Live monitor port (1024-65535). Default 8765.")
    parser.add_argument("--no-monitor", action="store_true", help="Disable the live HTTP monitor.")
    args = parser.parse_args()

    cfg = _load_config(args.config)
    run_id = str(uuid.uuid4())[:8]
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    setup_logging(cfg, run_id)
    logger = logging.getLogger("ajsaa")

    if args.dry_run:
        cfg.setdefault("storage", {})["provider"] = "local"
        logger.info("Dry-run mode — storage writes disabled")

    logger.info("=" * 60)
    logger.info("AJSAA run starting  [run_id=%s]", run_id)

    monitor = None
    if not args.no_monitor:
        try:
            from agent.graph import set_live_state_writer
            from monitoring.web_monitoring.live_server import LiveMonitor
            monitor = LiveMonitor(port=args.port)
            url = monitor.start()
            logger.info("Live monitor: %s  (run_id=%s)", url, run_id)
            print(f"🌐 Live monitor: {url}  (run_id={run_id})", flush=True)
            set_live_state_writer(monitor.update_state)
        except RuntimeError as exc:
            logger.warning("Live monitor disabled: %s", exc)
            monitor = None

    from agent.graph import build_graph, set_live_state_writer
    graph = build_graph()
    initial_state = _build_initial_state(cfg, run_id, ts)

    try:
        final_state, node_timings, run_duration = _run_pipeline(graph, initial_state, run_id, ts)

        from providers.llm import usage_tracker
        final_state["token_usage"] = usage_tracker.snapshot()
        logger.info(_format_token_summary_line(final_state["token_usage"]))

        _write_reports(final_state, node_timings, run_duration, run_id, ts, logger)

        if monitor is not None:
            _push_final_state(monitor, final_state, node_timings)
    finally:
        set_live_state_writer(None)
        if monitor is not None:
            monitor.stop()

    logger.info("Run complete — %d new jobs stored", final_state.get("stored_count", 0))
    if final_state.get("errors"):
        for err in final_state["errors"]:
            logger.error("  ERROR: %s", err)
    logger.info("=" * 60)

    if final_state.get("errors"):
        sys.exit(1)


def _format_token_summary_line(snapshot: dict) -> str:
    return format_token_summary(snapshot)


if __name__ == "__main__":
    main()
