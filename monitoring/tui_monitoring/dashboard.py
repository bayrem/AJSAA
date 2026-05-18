"""Rich terminal dashboard rendered during the pipeline run."""
from __future__ import annotations

from typing import Callable

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from monitoring.monitoring_core.constants import NODE_ORDER
from monitoring.monitoring_core.token_summary import format_footer_tokens

_STATUS_SYMBOLS = {
    "done": ("✓", "green"),
    "error": ("✗", "red"),
    "running": ("⟳", "yellow"),
    "waiting": ("○", "dim"),
}


def _analyze_kpis(updates: dict) -> tuple[str, str]:
    scored = updates.get("scored_jobs", [])
    apply_n = sum(1 for j in scored if j.get("recommendation") == "APPLY")
    consider_n = sum(1 for j in scored if j.get("recommendation") == "CONSIDER")
    return (f"Passed: {len(scored)}", f"APPLY: {apply_n}  CONSIDER: {consider_n}")


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


def extract_kpis(node_name: str, updates: dict) -> tuple[str, str]:
    extractor = _KPI_EXTRACTORS.get(node_name)
    return extractor(updates) if extractor else ("—", "—")


def make_dashboard(
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
        glyph, colour = _STATUS_SYMBOLS.get(status, _STATUS_SYMBOLS["waiting"])
        table.add_row(node, kpi1, kpi2, Text(glyph, style=colour), time_str)

    return table


def make_live_view(
    statuses: dict,
    kpis: dict,
    timings: dict,
    run_id: str,
    ts: str,
    token_snapshot: dict,
) -> RenderableType:
    table = make_dashboard(statuses, kpis, timings, run_id, ts)
    footer = Text(format_footer_tokens(token_snapshot), style="dim")
    return Group(table, footer)
