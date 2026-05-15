"""After-action HTML report generator for AJSAA runs."""
import html as _html
import json
import re
from datetime import datetime
from pathlib import Path

_LOGS_DIR = Path("logs")
_RUNS_DIR = _LOGS_DIR / "runs"

_NODE_ORDER = [
    "load_context", "convert_cvs", "generate_queries", "search_jobs",
    "search_companies", "analyze_jobs", "store_results", "send_notifications",
]


def _fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m{s:02d}s"


def _score_color(score: int) -> str:
    if score >= 80:
        return "#28a745"
    if score >= 60:
        return "#ffc107"
    return "#dc3545"


def _strip_html(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html).strip()


def _safe_url(url: str) -> str:
    """Allow only http/https URLs; fall back to '#' for anything else (e.g. javascript:)."""
    stripped = url.strip()
    if stripped.startswith(("http://", "https://")):
        return _html.escape(stripped, quote=True)
    return "#"


def _job_card_html(job: dict) -> str:
    score = job.get("score", 0)
    rec = _html.escape(job.get("recommendation", ""))
    color = _score_color(score)
    title = _html.escape(job.get("title", ""))
    company = _html.escape(job.get("company", ""))
    location = _html.escape(job.get("location", ""))
    url = _safe_url(job.get("url", ""))
    summary = _html.escape(job.get("summary", ""))
    summary_p = f'<p style="margin:6px 0 0;font-size:13px;color:#495057;">{summary}</p>' if summary else ""
    return (
        '<div style="border:1px solid #dee2e6;border-radius:6px;padding:12px;margin-bottom:10px;">'
        '<div style="display:flex;justify-content:space-between;align-items:flex-start;">'
        "<div>"
        f'<a href="{url}" target="_blank" style="font-weight:bold;font-size:14px;text-decoration:none;color:#0d6efd;">{title}</a>'
        f'<span style="margin-left:8px;color:#6c757d;font-size:13px;">{company} · {location}</span>'
        "</div>"
        '<div style="display:flex;gap:6px;flex-shrink:0;">'
        f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:12px;font-size:12px;font-weight:bold;">{score}</span>'
        f'<span style="background:#e9ecef;padding:2px 8px;border-radius:12px;font-size:12px;">{rec}</span>'
        "</div></div>"
        f"{summary_p}"
        "</div>"
    )


def _node_row_html(name: str, node_timings: dict) -> str:
    elapsed = node_timings.get(name)
    time_str = f"{elapsed:.1f}s" if elapsed is not None else "—"
    status = "✓" if elapsed is not None else "○"
    return f"<tr><td>{name}</td><td>{status}</td><td>{time_str}</td></tr>"


def generate_run_report(state: dict, duration_s: float, node_timings: dict) -> Path:
    """Write logs/runs/run_{ts}_{run_id}.html and return the path."""
    _RUNS_DIR.mkdir(parents=True, exist_ok=True)
    run_id = state.get("run_id", "unknown")
    ts = state.get("timestamp", "")
    ts_file = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = _RUNS_DIR / f"run_{ts_file}_{run_id}.html"

    scored = state.get("scored_jobs", [])
    sorted_jobs = sorted(scored, key=lambda j: j.get("score", 0), reverse=True)
    errors = state.get("errors", [])

    job_cards = "\n".join(_job_card_html(j) for j in sorted_jobs)
    node_rows = "\n".join(_node_row_html(n, node_timings) for n in _NODE_ORDER)
    errors_display = "none" if not errors else "block"
    errors_list = "\n".join(f"<li>{e}</li>" for e in errors)
    no_jobs_msg = "" if sorted_jobs else '<p style="color:#6c757d">No jobs stored this run.</p>'

    html = "\n".join([
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        f"<title>AJSAA Run {run_id}</title>",
        "<style>",
        "body{font-family:system-ui,sans-serif;max-width:900px;margin:40px auto;padding:0 20px;color:#212529;}",
        "h1{font-size:20px;margin-bottom:4px;}",
        ".meta{color:#6c757d;font-size:13px;margin-bottom:24px;}",
        "table{width:100%;border-collapse:collapse;margin-bottom:24px;font-size:13px;}",
        "th{background:#f8f9fa;text-align:left;padding:8px 10px;border-bottom:2px solid #dee2e6;}",
        "td{padding:7px 10px;border-bottom:1px solid #f0f0f0;}",
        ".errors{background:#fff3cd;border:1px solid #ffc107;border-radius:6px;padding:12px;margin-bottom:24px;}",
        "h2{font-size:16px;margin:24px 0 12px;}",
        "a{color:#0d6efd;text-decoration:none;}",
        "</style>",
        "</head>",
        "<body>",
        f"<h1>AJSAA — Run {run_id}</h1>",
        f'<div class="meta">{ts} · Duration: {_fmt_duration(duration_s)} · Jobs stored: {state.get("stored_count", 0)}</div>',
        "<h2>Pipeline</h2>",
        "<table>",
        "<thead><tr><th>Node</th><th>Status</th><th>Time</th></tr></thead>",
        "<tbody>",
        node_rows,
        "</tbody></table>",
        f'<div class="errors" style="display:{errors_display}">',
        f"<strong>Errors ({len(errors)})</strong><ul>{errors_list}</ul>",
        "</div>",
        f"<h2>Jobs stored this run ({len(sorted_jobs)})</h2>",
        job_cards,
        no_jobs_msg,
        "</body>",
        "</html>",
    ])

    out_path.write_text(html, encoding="utf-8")
    return out_path


_INDEX_ROW_MARKER = "<!-- ROWS -->"

_INDEX_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>AJSAA Runs</title>
<style>
body{font-family:system-ui,sans-serif;max-width:1100px;margin:40px auto;padding:0 20px;color:#212529;}
h1{font-size:20px;margin-bottom:20px;}
table{width:100%;border-collapse:collapse;font-size:13px;}
th{background:#f8f9fa;text-align:left;padding:8px 10px;border-bottom:2px solid #dee2e6;}
td{padding:7px 10px;border-bottom:1px solid #f0f0f0;}
a{color:#0d6efd;text-decoration:none;}
</style>
</head>
<body>
<h1>AJSAA — All Runs</h1>
<table>
<thead><tr><th>Run ID</th><th>Date</th><th>Duration</th><th>Queries</th><th>Found</th><th>Passed</th><th>New saved</th><th>Errors</th><th></th></tr></thead>
<tbody>
<!-- ROWS -->
</tbody>
</table>
</body>
</html>"""


def update_index(run_id: str, timestamp: str, duration_s: float, stats: dict) -> None:
    """Prepend a new row to logs/index.html (newest first). Creates the file if missing."""
    _LOGS_DIR.mkdir(parents=True, exist_ok=True)
    index_path = _LOGS_DIR / "index.html"

    matches = sorted(_RUNS_DIR.glob(f"run_*_{run_id}.html")) if _RUNS_DIR.exists() else []
    detail_href = f"runs/{matches[-1].name}" if matches else "#"

    new_row = (
        f"<tr>"
        f"<td>{run_id}</td>"
        f"<td>{timestamp}</td>"
        f"<td>{_fmt_duration(duration_s)}</td>"
        f"<td>{stats.get('queries', 0)}</td>"
        f"<td>{stats.get('found', 0)}</td>"
        f"<td>{stats.get('passed', 0)}</td>"
        f"<td>{stats.get('new_saved', 0)}</td>"
        f"<td>{stats.get('errors', 0)}</td>"
        f'<td><a href="{detail_href}">→</a></td>'
        f"</tr>"
    )

    if not index_path.exists():
        content = _INDEX_TEMPLATE.replace(_INDEX_ROW_MARKER, f"{new_row}\n{_INDEX_ROW_MARKER}")
    else:
        content = index_path.read_text(encoding="utf-8")
        content = content.replace(_INDEX_ROW_MARKER, f"{new_row}\n{_INDEX_ROW_MARKER}")

    index_path.write_text(content, encoding="utf-8")


def append_runs_json(run_id: str, timestamp: str, duration_s: float, stats: dict) -> None:
    """Append a run entry to logs/runs.json."""
    _LOGS_DIR.mkdir(parents=True, exist_ok=True)
    runs_json = _LOGS_DIR / "runs.json"

    entry = {"run_id": run_id, "timestamp": timestamp, "duration_s": round(duration_s, 1), **stats}

    data = json.loads(runs_json.read_text(encoding="utf-8")) if runs_json.exists() else []
    data.append(entry)
    runs_json.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
