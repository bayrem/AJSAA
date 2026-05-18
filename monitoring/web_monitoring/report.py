"""After-action HTML report generator for AJSAA runs."""
import html as _html
import json
from datetime import datetime
from pathlib import Path

from monitoring.monitoring_core.constants import NODE_ORDER
from monitoring.monitoring_core.formatters import (
    fmt_cost,
    fmt_duration,
    fmt_tokens,
    safe_float,
    safe_int,
)

_LOGS_DIR = Path("logs")
_RUNS_DIR = _LOGS_DIR / "runs"


def _token_block_html(token_usage: dict) -> str:
    if not token_usage:
        return (
            "<h2>Token spend</h2>"
            '<p style="color:#6c757d">— no LLM calls recorded this run.</p>'
        )

    grand = token_usage.get("grand_total") or {}
    by_model = token_usage.get("by_model") or {}
    by_node = token_usage.get("by_node") or {}

    g_in = safe_int(grand.get("input_tokens"))
    g_out = safe_int(grand.get("output_tokens"))
    g_calls = safe_int(grand.get("calls"))
    g_cost = safe_float(grand.get("cost_usd"))

    grand_line = (
        f'<p style="font-size:14px;margin:8px 0 16px;">'
        f"<strong>Grand total:</strong> {fmt_cost(g_cost)} · "
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
    return sorted(store.items(), key=lambda kv: safe_float(kv[1].get("cost_usd")), reverse=True)


def _usage_row_html(name: str, entry: dict) -> str:
    calls = safe_int(entry.get("calls"))
    in_tok = safe_int(entry.get("input_tokens"))
    out_tok = safe_int(entry.get("output_tokens"))
    cost = safe_float(entry.get("cost_usd"))
    return (
        "<tr>"
        f"<td>{_html.escape(str(name))}</td>"
        f"<td>{calls}</td>"
        f"<td>{fmt_tokens(in_tok + out_tok)} ({fmt_tokens(in_tok)} in / {fmt_tokens(out_tok)} out)</td>"
        f"<td>{fmt_cost(cost)}</td>"
        "</tr>"
    )


def _score_color(score: int) -> str:
    if score >= 80:
        return "#28a745"
    if score >= 60:
        return "#ffc107"
    return "#dc3545"


def _safe_url(url: str) -> str:
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


def _node_row_html(name: str, node_timings: dict, by_node: dict) -> str:
    elapsed = node_timings.get(name)
    time_str = f"{elapsed:.1f}s" if elapsed is not None else "—"
    status = "✓" if elapsed is not None else "○"
    node_data = by_node.get(name) or {}
    in_tok = safe_int(node_data.get("input_tokens"))
    out_tok = safe_int(node_data.get("output_tokens"))
    total_tokens = in_tok + out_tok
    cost = safe_float(node_data.get("cost_usd"))
    tok_str = fmt_tokens(total_tokens) if total_tokens else "—"
    cost_str = fmt_cost(cost) if cost else "—"
    return (
        f"<tr><td>{name}</td><td>{status}</td><td>{time_str}</td>"
        f"<td>{tok_str}</td><td>{cost_str}</td></tr>"
    )


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
  function fmtTokens(n){
    if(!n) return '—';
    if(n < 1000) return String(n);
    if(n < 10000) return (n/1000).toFixed(1)+'k';
    return Math.round(n/1000)+'k';
  }
  function fmtCost(c){
    if(!c) return '—';
    if(c < 0.01) return '$'+c.toFixed(4);
    return '$'+c.toFixed(2);
  }
  function renderNodeRows(state){
    var order = ['load_context','convert_cvs','generate_queries','search_jobs',
                 'search_companies','analyze_jobs','store_results','send_notifications'];
    var ns = state.node_status || {};
    var nt = state.node_timings || {};
    var bn = (state.token_usage || {}).by_node || {};
    var rows = '';
    for (var i=0; i<order.length; i++){
      var name = order[i];
      var st = ns[name] || 'waiting';
      var t = nt[name];
      var glyph = st === 'complete' ? '✓' : st === 'error' ? '✗'
                : st === 'running' ? '⟳' : '○';
      var timeStr = (typeof t === 'number') ? t.toFixed(1) + 's' : '—';
      var nd = bn[name] || {};
      var toks = (nd.input_tokens||0) + (nd.output_tokens||0);
      rows += '<tr><td>' + escapeHtml(name) + '</td><td>' + glyph
           +  '</td><td>' + timeStr + '</td><td>' + fmtTokens(toks)
           +  '</td><td>' + fmtCost(nd.cost_usd||0) + '</td></tr>';
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
    """Build the dashboard HTML used by both the live page and the static report."""
    run_id = state.get("run_id", "unknown")
    ts = state.get("timestamp", "")

    scored = state.get("scored_jobs", [])
    sorted_jobs = sorted(scored, key=lambda j: j.get("score", 0), reverse=True)
    errors = state.get("errors", [])

    job_cards = "\n".join(_job_card_html(j) for j in sorted_jobs)
    by_node = (state.get("token_usage") or {}).get("by_node") or {}
    node_rows = "\n".join(_node_row_html(n, node_timings, by_node) for n in NODE_ORDER)
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
        f'<div class="meta">{_html.escape(str(ts))} · Duration: {fmt_duration(duration_s)} '
        f'· Jobs stored: {state.get("stored_count", 0)}</div>',
        '<div id="dashboard">',
        "<h2>Pipeline</h2>",
        "<table>",
        "<thead><tr><th>Node</th><th>Status</th><th>Time</th><th>Tokens</th><th>Cost</th></tr></thead>",
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
    """Write logs/runs/run_{ts}_{run_id}.html and return the path."""
    _RUNS_DIR.mkdir(parents=True, exist_ok=True)
    run_id = state.get("run_id", "unknown")
    ts_file = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = _RUNS_DIR / f"run_{ts_file}_{run_id}.html"

    status = "failed" if state.get("errors") else "complete"
    html = render_dashboard_html(state, duration_s, node_timings, live=False, status=status)
    out_path.write_text(html, encoding="utf-8")
    return out_path


_RUNS_JSON_PLACEHOLDER = "__RUNS_JSON__"
_ROWS_HTML_PLACEHOLDER = "__ROWS_HTML__"

_INDEX_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>AJSAA Runs</title>
<style>
body{font-family:system-ui,sans-serif;max-width:1200px;margin:40px auto;padding:0 20px;color:#212529;}
h1{font-size:20px;margin-bottom:20px;}
.chart-section{margin-bottom:28px;}
.chart-controls{display:flex;align-items:center;gap:8px;margin-bottom:8px;font-size:13px;}
select{font-size:13px;padding:3px 8px;border:1px solid #ced4da;border-radius:4px;background:#fff;}
#runs-chart{max-height:220px;}
table{width:100%;border-collapse:collapse;font-size:13px;}
th{background:#f8f9fa;text-align:left;padding:8px 10px;border-bottom:2px solid #dee2e6;}
td{padding:7px 10px;border-bottom:1px solid #f0f0f0;}
a{color:#0d6efd;text-decoration:none;}
.ok{color:#28a745;font-weight:bold;}
.fail{color:#dc3545;font-weight:bold;}
</style>
</head>
<body>
<h1>AJSAA — All Runs</h1>
<div class="chart-section">
  <div class="chart-controls">
    <label for="metric-select">Y axis:</label>
    <select id="metric-select">
      <option value="runtime">Run time</option>
      <option value="tokens">Tokens consumed</option>
      <option value="cost">Cost $</option>
      <option value="found">Jobs found</option>
      <option value="scored">Jobs scored</option>
      <option value="approved">Jobs approved</option>
    </select>
  </div>
  <canvas id="runs-chart"></canvas>
</div>
<table>
<thead><tr>
  <th>Run ID</th><th>Datetime</th><th>Status</th><th>Runtime</th>
  <th>Jobs found</th><th>Jobs scored</th><th>Jobs approved</th>
  <th>Tokens consumed</th><th>Cost $</th><th></th>
</tr></thead>
<tbody>
__ROWS_HTML__
</tbody>
</table>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<script>
(function(){
var RUNS = __RUNS_JSON__;
var METRICS = {
  runtime:{field:'duration_s',label:'Run time (s)'},
  tokens:{field:'tokens_total',label:'Tokens consumed'},
  cost:{field:'cost_usd',label:'Cost ($)'},
  found:{field:'found',label:'Jobs found'},
  scored:{field:'passed',label:'Jobs scored'},
  approved:{field:'new_saved',label:'Jobs approved'},
};
var sorted = RUNS.slice().sort(function(a,b){return a.timestamp<b.timestamp?-1:1;});
var chart = null;
function buildChart(metric){
  var m = METRICS[metric];
  var labels = sorted.map(function(r){return r.timestamp;});
  var data = sorted.map(function(r){return r[m.field]!=null?r[m.field]:null;});
  if(chart){
    chart.data.labels = labels;
    chart.data.datasets[0].data = data;
    chart.data.datasets[0].label = m.label;
    chart.options.scales.y.title.text = m.label;
    chart.update();
    return;
  }
  var ctx = document.getElementById('runs-chart').getContext('2d');
  chart = new Chart(ctx,{
    type:'line',
    data:{
      labels:labels,
      datasets:[{
        label:m.label,data:data,
        borderColor:'#0d6efd',
        backgroundColor:'rgba(13,110,253,0.1)',
        tension:0.2,spanGaps:false,pointRadius:4,
      }],
    },
    options:{
      responsive:true,
      plugins:{legend:{display:false}},
      scales:{
        x:{ticks:{maxTicksLimit:12}},
        y:{title:{display:true,text:m.label},beginAtZero:true},
      },
    },
  });
}
try{
  if(sorted.length>0){
    buildChart('runtime');
    document.getElementById('metric-select').addEventListener('change',function(){buildChart(this.value);});
  } else {
    document.querySelector('.chart-section').style.display='none';
  }
}catch(e){}
})();
</script>
</body>
</html>"""


def update_index(run_id: str, timestamp: str, duration_s: float, stats: dict) -> None:
    """Rebuild logs/index.html from runs.json. Must be called after append_runs_json."""
    _LOGS_DIR.mkdir(parents=True, exist_ok=True)
    index_path = _LOGS_DIR / "index.html"
    runs_json_path = _LOGS_DIR / "runs.json"

    all_runs = json.loads(runs_json_path.read_text(encoding="utf-8")) if runs_json_path.exists() else []

    rows: list[str] = []
    for run in reversed(all_runs):
        rid = run["run_id"]
        matches = sorted(_RUNS_DIR.glob(f"run_*_{rid}.html")) if _RUNS_DIR.exists() else []
        href = f"runs/{matches[-1].name}" if matches else "#"

        errors_n = run.get("errors", 0)
        status_cls = "ok" if errors_n == 0 else "fail"
        status_label = "✓ success" if errors_n == 0 else "✗ failed"

        tok_raw = run.get("tokens_total")
        tok_str = fmt_tokens(int(tok_raw)) if tok_raw is not None else "—"

        cost_raw = run.get("cost_usd")
        cost_str = fmt_cost(safe_float(cost_raw)) if cost_raw is not None else "—"

        rows.append(
            f"<tr>"
            f"<td>{_html.escape(str(rid))}</td>"
            f"<td>{_html.escape(str(run.get('timestamp', '')))}</td>"
            f'<td class="{status_cls}">{status_label}</td>'
            f"<td>{fmt_duration(safe_float(run.get('duration_s', 0)))}</td>"
            f"<td>{safe_int(run.get('found', 0))}</td>"
            f"<td>{safe_int(run.get('passed', 0))}</td>"
            f"<td>{safe_int(run.get('new_saved', 0))}</td>"
            f"<td>{tok_str}</td>"
            f"<td>{cost_str}</td>"
            f'<td><a href="{href}">→</a></td>'
            f"</tr>"
        )

    runs_json_literal = json.dumps(all_runs, ensure_ascii=False).replace("</", r"<\/")
    content = (
        _INDEX_TEMPLATE
        .replace(_ROWS_HTML_PLACEHOLDER, "\n".join(rows))
        .replace(_RUNS_JSON_PLACEHOLDER, runs_json_literal)
    )
    index_path.write_text(content, encoding="utf-8")


def append_runs_json(run_id: str, timestamp: str, duration_s: float, stats: dict) -> None:
    """Append a run entry to logs/runs.json."""
    _LOGS_DIR.mkdir(parents=True, exist_ok=True)
    runs_json = _LOGS_DIR / "runs.json"

    entry = {"run_id": run_id, "timestamp": timestamp, "duration_s": round(duration_s, 1), **stats}

    data = json.loads(runs_json.read_text(encoding="utf-8")) if runs_json.exists() else []
    data.append(entry)
    runs_json.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
