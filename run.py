"""AJSAA — main entry point."""
import argparse
import logging
import logging.handlers
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from rich.live import Live
from rich.table import Table
from rich.text import Text

load_dotenv()

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


def _extract_kpis(node_name: str, updates: dict) -> tuple[str, str]:
    if node_name == "load_context":
        return (f"CVs: {len(updates.get('cvs', []))}", f"Companies: {len(updates.get('companies', []))}")
    if node_name == "convert_cvs":
        return (f"Profiles: {len(updates.get('cvs', []))}", "")
    if node_name == "generate_queries":
        return (f"Queries: {len(updates.get('queries', []))}", "")
    if node_name == "search_jobs":
        return (f"Found: {len(updates.get('raw_jobs', []))}", "")
    if node_name == "search_companies":
        return (f"Found: {len(updates.get('raw_jobs', []))}", "")
    if node_name == "analyze_jobs":
        scored = updates.get("scored_jobs", [])
        apply_n = sum(1 for j in scored if j.get("recommendation") == "APPLY")
        consider_n = sum(1 for j in scored if j.get("recommendation") == "CONSIDER")
        return (f"Passed: {len(scored)}", f"APPLY: {apply_n}  CONSIDER: {consider_n}")
    if node_name == "store_results":
        return (f"New: {updates.get('stored_count', 0)}", "")
    if node_name == "send_notifications":
        sent = updates.get("notification_sent", False)
        return ("Sent: yes" if sent else "Sent: no", "")
    return ("—", "—")


def _make_dashboard(
    statuses: dict,
    kpis: dict,
    timings: dict,
    run_id: str,
    ts: str,
) -> Table:
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

        if status == "done":
            symbol: Text = Text("✓", style="green")
        elif status == "error":
            symbol = Text("✗", style="red")
        elif status == "running":
            symbol = Text("⟳", style="yellow")
        else:
            symbol = Text("○", style="dim")

        table.add_row(node, kpi1, kpi2, symbol, time_str)

    return table


def _setup_logging(cfg: dict, run_id: str) -> None:
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
        run_log = runs_dir / f"run_{ts_str}.log"
        file_handler = logging.FileHandler(run_log, encoding="utf-8")
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


def _load_config(path: str = "config.yaml") -> dict:
    import yaml
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _build_initial_state(cfg: dict, run_id: str, ts: str) -> dict:
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
        cfg["storage"]["provider"] = "local"
        logger.info("Dry-run mode — storage writes disabled")

    logger.info("=" * 60)
    logger.info("AJSAA run starting  [run_id=%s]", run_id)

    from agent.graph import build_graph
    graph = build_graph()
    initial_state = _build_initial_state(cfg, run_id, ts)

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
                elapsed = time.time() - node_start
                node_start = time.time()
                if node_name in NODE_ORDER:
                    prev_err_count = len(final_state.get("errors", []))
                    new_err_count = len(updates.get("errors", []))
                    statuses[node_name] = "error" if new_err_count > prev_err_count else "done"
                    kpis_display[node_name] = _extract_kpis(node_name, updates)
                    node_timings[node_name] = elapsed
                    try:
                        next_idx = NODE_ORDER.index(node_name) + 1
                        if next_idx < len(NODE_ORDER):
                            statuses[NODE_ORDER[next_idx]] = "running"
                    except ValueError:
                        pass
                final_state.update(updates)
                live.update(_make_dashboard(statuses, kpis_display, node_timings, run_id, ts))

    run_duration = time.time() - run_start

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

    logger.info("Run complete — %d new jobs stored", final_state.get("stored_count", 0))
    if final_state.get("errors"):
        for err in final_state["errors"]:
            logger.error("  ERROR: %s", err)
    logger.info("=" * 60)

    if final_state.get("errors"):
        sys.exit(1)


if __name__ == "__main__":
    main()
