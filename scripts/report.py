"""After-action HTML report generator for AJSAA runs."""
import html as _html
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

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


def _fmt_tokens(n: int) -> str:
    """Render an integer token count compactly (e.g. 14200 -> '14.2k')."""
    if n < 1000:
        return str(n)
    if n < 10_000:
        return f"{n / 1000:.1f}k"
    return f"{n / 1000:.0f}k"


def _fmt_cost(cost: float) -> str:
    """Render a USD cost. Uses 4 decimals under $0.01 so tiny costs aren't '$0.00'."""
    if cost == 0:
        return "$0.00"
    if cost < 0.01:
        return f"${cost:.4f}"
    return f"${cost:.2f}"


def _safe_int(value: Any) -> int:
    """Coerce ``value`` to ``int`` with a 0 default — providers may emit None."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    """Coerce ``value`` to ``float`` with a 0.0 default."""
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _token_block_html(token_usage: dict) -> str:
    """Render the post-pipeline 'Token spend' block.

    Graceful when ``token_usage`` is empty (no LLM calls in the run): we show a
    short '—' placeholder instead of an empty card, so the report layout stays
    consistent regardless of what the pipeline actually did.

    All model and node names are passed through :func:`html.escape` so an
    attacker-controlled model string (e.g. provider returns a weird name) can't
    inject HTML.
    """
    if not token_usage:
        return (
            "<h2>Token spend</h2>"
            '<p style="color:#6c757d">— no LLM calls recorded this run.</p>'
        )

    grand = token_usage.get("grand_total") or {}
    by_model = token_usage.get("by_model") or {}
    by_node = token_usage.get("by_node") or {}

    g_in = _safe_int(grand.get("input_tokens"))
    g_out = _safe_int(grand.get("output_tokens"))
    g_calls = _safe_int(grand.get("calls"))
    g_cost = _safe_float(grand.get("cost_usd"))

    grand_line = (
        f'<p style="font-size:14px;margin:8px 0 16px;">'
        f"<strong>Grand total:</strong> {_fmt_cost(g_cost)} · "
        f"{g_in:,} in / {g_out:,} out · {g_calls} calls"
        "</p>"
    )

    model_rows = "\n".join(_usage_row_html(name, entry) for name, entry in _sorted_by_cost(by_model))
    model_table = (
        "<h3 style='font-size:14px;margin:16px 0 8px;'>By model</h3>"
        "<table>"
        "<thead><tr><th>Model</th><th>Calls</th><th>Tokens</th><th>Cost</th></tr></thead>"
        f"<tbody>{model_rows}</tbody>"
        "</table>"
    ) if by_model else ""

    node_rows = "\n".join(_usage_row_html(name, entry) for name, entry in _sorted_by_cost(by_node))
    node_block = (
        "<details style='margin-top:8px;'>"
        "<summary style='cursor:pointer;font-weight:bold;font-size:13px;'>By node (click to expand)</summary>"
        "<table style='margin-top:8px;'>"
        "<thead><tr><th>Node</th><th>Calls</th><th>Tokens</th><th>Cost</th></tr></thead>"
        f"<tbody>{node_rows}</tbody>"
        "</table>"
        "</details>"
    ) if by_node else ""

    return f"<h2>Token spend</h2>{grand_line}{model_table}{node_block}"


def _sorted_by_cost(store: dict) -> list[tuple[str, dict]]:
    """Return ``store`` items sorted by ``cost_usd`` descending (biggest first)."""
    return sorted(store.items(), key=lambda kv: _safe_float(kv[1].get("cost_usd")), reverse=True)


def _usage_row_html(name: str, entry: dict) -> str:
    """Render one ``<tr>`` for the per-model / per-node usage tables."""
    calls = _safe_int(entry.get("calls"))
    in_tok = _safe_int(entry.get("input_tokens"))
    out_tok = _safe_int(entry.get("output_tokens"))
    cost = _safe_float(entry.get("cost_usd"))
    return (
        "<tr>"
        f"<td>{_html.escape(str(name))}</td>"
        f"<td>{calls}</td>"
        f"<td>{_fmt_tokens(in_tok + out_tok)} ({_fmt_tokens(in_tok)} in / {_fmt_tokens(out_tok)} out)</td>"
        f"<td>{_fmt_cost(cost)}</td>"
        "</tr>"
    )


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


_LIVE_PAGE_CSS = (
    "body{font-family:system-ui,sans-serif;max-width:900px;margin:40px auto;padding:0 20px;color:#212529;}"
    "h1{font-size:20px;margin-bottom:4px;}"
    ".meta{color:#6c757d;font-size:13px;margin-bottom:24px;}"
    "table{width:100%;border-collapse:collapse;margin-bottom:24px;font-size:13px;}"
    "th{background:#f8f9fa;text-align:left;padding:8px 10px;border-bottom:2px solid #dee2e6;}"
    "td{padding:7px 10px;border-bottom:1px solid #f0f0f0;}"
    ".errors{background:#fff3cd;border:1px solid #ffc107;border-radius:6px;padding:12px;margin-bottom:24px;}"
    "h2{font-size:16px;margin:24px 0 12px;}"
    "a{color:#0d6efd;text-decoration:none;}"
    ".badge{display:inline-block;padding:3px 10px;border-radius:12px;color:#fff;"
    "font-size:12px;font-weight:bold;margin-left:8px;vertical-align:middle;}"
    ".badge-running{background:#0d6efd;animation:pulse 1.4s ease-in-out infinite;}"
    ".badge-complete{background:#28a745;}"
    ".badge-failed{background:#dc3545;}"
    "@keyframes pulse{0%,100%{opacity:1}50%{opacity:.45}}"
)


# Pure-vanilla JS poll. Replaces #dashboard innerHTML each second from the
# JSON snapshot served at /state.json. Stops on first non-"running" status.
# Kept inline (no external CDN) because the page is served on 127.0.0.1
# without internet assumptions — also fewer moving parts to secure.
_LIVE_POLL_JS = """<script>
(function(){
  function badgeHtml(status){
    var cls = 'badge-' + (status || 'running');
    var label = (status || 'running').toUpperCase();
    return '<span class="badge ' + cls + '">' + label + '</span>';
  }
  function escapeHtml(s){
    return String(s || '').replace(/[&<>"']/g, function(c){
      return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c];
    });
  }
  function renderNodeRows(state){
    var order = ['load_context','convert_cvs','generate_queries','search_jobs',
                 'search_companies','analyze_jobs','store_results','send_notifications'];
    var ns = state.node_status || {};
    var nt = state.node_timings || {};
    var rows = '';
    for (var i=0; i<order.length; i++){
      var name = order[i];
      var st = ns[name] || 'waiting';
      var t = nt[name];
      var glyph = st === 'complete' ? '✓' : st === 'error' ? '✗'
                : st === 'running' ? '⟳' : '○';
      var timeStr = (typeof t === 'number') ? t.toFixed(1) + 's' : '—';
      rows += '<tr><td>' + escapeHtml(name) + '</td><td>' + glyph
           +  '</td><td>' + timeStr + '</td></tr>';
    }
    return rows;
  }
  function tick(){
    fetch('/state.json', {cache: 'no-store'})
      .then(function(r){ return r.json(); })
      .then(function(state){
        var badge = document.getElementById('status-badge');
        if (badge) badge.outerHTML = '<span id="status-badge">' + badgeHtml(state.status) + '</span>';
        var tbody = document.getElementById('pipeline-rows');
        if (tbody) tbody.innerHTML = renderNodeRows(state);
        if ((state.status || 'running') !== 'running') return;
        setTimeout(tick, 1000);
      })
      .catch(function(){ setTimeout(tick, 2000); });
  }
  tick();
})();
</script>"""


def _badge_html(status: str) -> str:
    """Render the header status pill (running/complete/failed)."""
    safe = status if status in ("running", "complete", "failed") else "running"
    label = _html.escape(safe.upper())
    return f'<span class="badge badge-{safe}">{label}</span>'


def render_dashboard_html(
    state: dict,
    duration_s: float,
    node_timings: dict,
    live: bool = False,
    status: str = "complete",
) -> str:
    """Build the dashboard HTML string used by both the live page and the static report.

    The two callers differ only by:
      - ``live=True``  embeds the JS poll block; ``status`` defaults to "running" in this case.
      - ``live=False`` writes the JS block as empty string; ``status`` is "complete" or "failed".

    Kept in one function so the live page and the post-run static report can't
    drift visually. Both versions use the same CSS, table layout, token block,
    and job-card markup.
    """
    run_id = state.get("run_id", "unknown")
    ts = state.get("timestamp", "")

    scored = state.get("scored_jobs", [])
    sorted_jobs = sorted(scored, key=lambda j: j.get("score", 0), reverse=True)
    errors = state.get("errors", [])

    job_cards = "\n".join(_job_card_html(j) for j in sorted_jobs)
    node_rows = "\n".join(_node_row_html(n, node_timings) for n in _NODE_ORDER)
    errors_display = "none" if not errors else "block"
    errors_list = "\n".join(f"<li>{_html.escape(str(e))}</li>" for e in errors)
    no_jobs_msg = "" if sorted_jobs else '<p style="color:#6c757d">No jobs stored this run.</p>'
    token_block = _token_block_html(state.get("token_usage") or {})
    poll_js = _LIVE_POLL_JS if live else ""
    badge = _badge_html(status)

    parts = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        f"<title>AJSAA Run {_html.escape(str(run_id))}</title>",
        f"<style>{_LIVE_PAGE_CSS}</style>",
        "</head>",
        "<body>",
        f'<h1>AJSAA — Run {_html.escape(str(run_id))} <span id="status-badge">{badge}</span></h1>',
        f'<div class="meta">{_html.escape(str(ts))} · Duration: {_fmt_duration(duration_s)} '
        f'· Jobs stored: {state.get("stored_count", 0)}</div>',
        '<div id="dashboard">',
        "<h2>Pipeline</h2>",
        "<table>",
        "<thead><tr><th>Node</th><th>Status</th><th>Time</th></tr></thead>",
        '<tbody id="pipeline-rows">',
        node_rows,
        "</tbody></table>",
        token_block,
        f'<div class="errors" style="display:{errors_display}">',
        f"<strong>Errors ({len(errors)})</strong><ul>{errors_list}</ul>",
        "</div>",
        f"<h2>Jobs stored this run ({len(sorted_jobs)})</h2>",
        job_cards,
        no_jobs_msg,
        "</div>",
        poll_js,
        "</body>",
        "</html>",
    ]
    return "\n".join(parts)


def generate_run_report(state: dict, duration_s: float, node_timings: dict) -> Path:
    """Write logs/runs/run_{ts}_{run_id}.html and return the path.

    Static post-run variant — calls :func:`render_dashboard_html` with
    ``live=False`` so the JS poll block is omitted and the page is a durable
    artefact.
    """
    _RUNS_DIR.mkdir(parents=True, exist_ok=True)
    run_id = state.get("run_id", "unknown")
    ts_file = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = _RUNS_DIR / f"run_{ts_file}_{run_id}.html"

    status = "failed" if state.get("errors") else "complete"
    html = render_dashboard_html(state, duration_s, node_timings, live=False, status=status)
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
<thead><tr><th>Run ID</th><th>Date</th><th>Duration</th><th>Queries</th><th>Found</th><th>Passed</th><th>New saved</th><th>Errors</th><th>Cost</th><th></th></tr></thead>
<tbody>
<!-- ROWS -->
</tbody>
</table>
</body>
</html>"""


_INDEX_LEGACY_HEADER = (
    "<thead><tr><th>Run ID</th><th>Date</th><th>Duration</th>"
    "<th>Queries</th><th>Found</th><th>Passed</th>"
    "<th>New saved</th><th>Errors</th><th></th></tr></thead>"
)
_INDEX_NEW_HEADER = (
    "<thead><tr><th>Run ID</th><th>Date</th><th>Duration</th>"
    "<th>Queries</th><th>Found</th><th>Passed</th>"
    "<th>New saved</th><th>Errors</th><th>Cost</th><th></th></tr></thead>"
)


def _migrate_legacy_index(content: str) -> str:
    """Upgrade an older index.html in place: add the Cost column.

    Older runs (pre-#61) wrote rows with 9 ``<td>`` cells (last one is the
    detail link). We splice a ``<td>—</td>`` placeholder in just before the
    final link cell so the column count matches the new 10-column header.
    Detection is cheap: if the header already declares Cost we do nothing.
    """
    if "<th>Cost</th>" in content:
        return content
    # Swap header in place.
    content = content.replace(_INDEX_LEGACY_HEADER, _INDEX_NEW_HEADER)
    # Patch every legacy row: insert an em-dash cell before the link cell.
    # Pattern matches the trailing `<td><a href="...">→</a></td></tr>` shape
    # the legacy template produced.
    legacy_row_re = re.compile(
        r'(<td><a href="[^"]*">→</a></td></tr>)'
    )
    return legacy_row_re.sub(r"<td>—</td>\1", content)


def update_index(run_id: str, timestamp: str, duration_s: float, stats: dict) -> None:
    """Prepend a new row to logs/index.html (newest first). Creates the file if missing.

    Legacy index files (pre-#61, no Cost column) are migrated on first write:
    the header is swapped and existing rows get a ``—`` Cost cell so columns
    line up. After migration, every subsequent write is a plain prepend.
    """
    _LOGS_DIR.mkdir(parents=True, exist_ok=True)
    index_path = _LOGS_DIR / "index.html"

    matches = sorted(_RUNS_DIR.glob(f"run_*_{run_id}.html")) if _RUNS_DIR.exists() else []
    detail_href = f"runs/{matches[-1].name}" if matches else "#"

    cost = stats.get("cost_usd")
    cost_cell = _fmt_cost(_safe_float(cost)) if cost is not None else "—"

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
        f"<td>{cost_cell}</td>"
        f'<td><a href="{detail_href}">→</a></td>'
        f"</tr>"
    )

    if not index_path.exists():
        content = _INDEX_TEMPLATE.replace(_INDEX_ROW_MARKER, f"{new_row}\n{_INDEX_ROW_MARKER}")
    else:
        content = index_path.read_text(encoding="utf-8")
        content = _migrate_legacy_index(content)
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
