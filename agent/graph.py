"""LangGraph pipeline definition for AJSAA.

Builds the directed graph of nodes shown below. Every node is wrapped in
``_safe`` so an unhandled exception inside a node is captured to
``state["errors"]`` rather than crashing the whole pipeline — partial runs
are more useful than no run at all.

Graph topology::

    load_context
        │
        ├──(pdfs?)──> convert_cvs ──┐
        │                           │
        ├──(no pdfs)────────────────┼──> generate_queries
        │                           │       │
        │                           │       └──(have queries?)──> search_jobs
        │                           │
        v                           v
    search_jobs ──> search_companies ──> aggregate_jobs ──> analyze_jobs ──> store_results
                                                              │
                                                              ├──(notify enabled?)──> send_notifications
                                                              │
                                                              v
                                                             END
"""
import logging
import time
from typing import Any, Callable

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agent.nodes.aggregate_jobs import run as aggregate_jobs
from agent.nodes.analyze_jobs import run as analyze_jobs
from agent.nodes.convert_cvs import run as convert_cvs
from agent.nodes.generate_queries import run as generate_queries
from agent.nodes.load_context import run as load_context
from agent.nodes.search_companies import run as search_companies
from agent.nodes.search_jobs import run as search_jobs
from agent.nodes.send_notifications import run as send_notifications
from agent.nodes.store_results import run as store_results
from agent.state import AgentState
from providers.llm import usage_tracker

logger = logging.getLogger(__name__)


# ── Live-monitor registration ────────────────────────────────────────────────
#
# The graph module must NOT import ``LiveMonitor`` directly — that would pull
# the http.server stack into every test that builds a graph in isolation
# (search_jobs / analyze_jobs etc.). Instead we expose a tiny register that
# ``run.py`` populates with a callable; the wrapper calls it when present,
# skips it when not. Anything duck-typed with ``update_state(dict)`` works.

_LiveStateWriter = Callable[[dict], None]
_current_live_writer: _LiveStateWriter | None = None


def set_live_state_writer(writer: _LiveStateWriter | None) -> None:
    """Register (or clear) the live-state writer called after each node.

    ``writer`` receives a snapshot dict shaped per the live-monitor schema.
    Pass ``None`` to disable — tests that don't care about the live view, and
    the ``--no-monitor`` codepath, both leave this unset.
    """
    global _current_live_writer
    _current_live_writer = writer


# Same canonical order used by run.py / scripts.report — duplicating the list
# locally avoids a circular import at module-load time.
_NODE_ORDER = [
    "load_context", "convert_cvs", "generate_queries", "search_jobs",
    "search_companies", "aggregate_jobs", "analyze_jobs", "store_results", "send_notifications",
]


def _build_live_snapshot(
    state: Any,
    current_node: str,
    status: str,
    node_status: dict[str, str],
) -> dict[str, Any]:
    """Assemble the dict pushed to the live monitor after each node.

    Reads the live token-usage snapshot lazily so the live page sees the same
    cost numbers the TUI footer is showing.
    """
    # Compute per-node elapsed times from wall-clock records kept by _safe().
    # Running nodes get a live elapsed; completed nodes get their final time.
    node_timings: dict[str, float] = {}
    for n, end_t in _node_end_times.items():
        start_t = _node_start_times.get(n)
        if start_t is not None:
            node_timings[n] = round(end_t - start_t, 2)

    return {
        "run_id": state.get("run_id", "unknown"),
        "timestamp": state.get("timestamp", ""),
        "run_start_time": state.get("run_start_time"),  # Unix ts for JS duration counter
        "status": status,
        "current_node": current_node,
        "node_status": dict(node_status),
        "node_timings": node_timings,
        "node_start_times": dict(_node_start_times),   # JS uses these for live per-node timers
        "kpis": {
            "raw_jobs": len(state.get("raw_jobs", [])),
            "scored_jobs": len(state.get("scored_jobs", [])),
            "discarded_jobs": len(state.get("discarded_jobs", [])),
            "stored_count": state.get("stored_count", 0),
            # Per-node jobs-treated counts shown in the pipeline table
            "jobs_treated": {
                "search_jobs": len(state.get("raw_jobs", [])),
                "search_companies": len(state.get("raw_jobs", [])),
                "analyze_jobs": len(state.get("scored_jobs", [])) + len(state.get("discarded_jobs", [])),
                "store_results": state.get("stored_count", 0),
            },
        },
        "token_usage": usage_tracker.snapshot(),
        "errors": list(state.get("errors", [])),
        "scored_jobs": list(state.get("scored_jobs", [])),
        "discarded_jobs": list(state.get("discarded_jobs", [])),
    }


# Per-graph-build accumulators. Reset by ``build_graph`` each run.
_node_status: dict[str, str] = {}
_node_start_times: dict[str, float] = {}   # Unix timestamp when node started
_node_end_times: dict[str, float] = {}     # Unix timestamp when node finished


# ── Safety wrapper ───────────────────────────────────────────────────────────

def _safe(node_fn, name: str):
    """Wrap a node so an unhandled exception records to state instead of crashing.

    The wrapped function logs the traceback at ERROR level (so it ends up in
    the log file) and appends a brief one-line error to ``state["errors"]``
    so the dashboard can mark the node as failed without losing prior work.

    It also sets/unsets the current node name on the usage tracker so every
    LLM call made inside this node gets attributed to it in the per-node
    cost breakdown. The unset in ``finally`` guarantees attribution doesn't
    leak past a node crash.
    """
    def wrapper(state: AgentState) -> AgentState:
        usage_tracker.set_node(name)
        _node_status[name] = "running"
        _node_start_times[name] = time.time()
        # Push the "running" snapshot before the node executes so the live page
        # sees the transition immediately, not just at completion.
        _push_live_snapshot(state, name, status="running")
        try:
            result = node_fn(state)
        except Exception as exc:
            logger.error("Node '%s' crashed: %s", name, exc, exc_info=True)
            errors = list(state.get("errors", []))
            errors.append(f"Node '{name}' crashed: {exc}")
            # AgentState is a TypedDict; ``**state`` widens to dict[str, object]
            # under mypy. Cast back so the wrapper signature stays honest.
            crashed: AgentState = {**state, "errors": errors}  # type: ignore[typeddict-item]
            _node_status[name] = "error"
            _node_end_times[name] = time.time()
            _push_live_snapshot(crashed, name, status="running")
            return crashed
        finally:
            usage_tracker.set_node(None)

        # Successful completion: mark done unless the node itself appended
        # a new error (partial failure). The completed snapshot includes the
        # node's own state mutations so the live page reflects fresh KPIs.
        _node_end_times[name] = time.time()
        merged = {**state, **result}
        prev_err = len(state.get("errors", []))
        new_err = len(merged.get("errors", []))
        _node_status[name] = "error" if new_err > prev_err else "complete"
        _push_live_snapshot(merged, name, status="running")
        return result

    # Preserve the node name so the dashboard's per-node lookup still works.
    wrapper.__name__ = name
    return wrapper


def _push_live_snapshot(state: Any, current_node: str, status: str) -> None:
    """Send a live-state snapshot to the registered writer, if any.

    Swallows writer exceptions — the pipeline must NEVER fail because the
    observability layer is broken. Logged at debug level so a real bug isn't
    completely silent if you go looking for it.
    """
    writer = _current_live_writer
    if writer is None:
        return
    try:
        snapshot = _build_live_snapshot(state, current_node, status, _node_status)
        writer(snapshot)
    except Exception:
        logger.debug("Live-state writer raised — continuing", exc_info=True)


# ── Routing predicates ───────────────────────────────────────────────────────

def _needs_convert_cvs(state: AgentState) -> str:
    """Skip PDF conversion when no PDFs were queued."""
    return "convert_cvs" if state["pdf_paths"] else "generate_queries"



def _needs_notifications(state: AgentState) -> str:
    """Skip the notifications node when no channels are configured."""
    cfg = state["config"]
    if cfg.get("notifications", {}).get("enabled") and cfg["notifications"].get("channels"):
        return "send_notifications"
    return END


# ── Graph builder ────────────────────────────────────────────────────────────

def build_graph() -> CompiledStateGraph:
    """Construct and compile the AJSAA pipeline graph."""
    # Reset the per-build node-status accumulator so the live page starts
    # from a clean slate each run; otherwise re-running ``main()`` in a test
    # would inherit "complete" markers from the previous run.
    _node_status.clear()
    _node_start_times.clear()
    _node_end_times.clear()
    for _n in _NODE_ORDER:
        _node_status[_n] = "waiting"

    graph = StateGraph(AgentState)

    # Register every node wrapped in the safety net.
    graph.add_node("load_context",       _safe(load_context,       "load_context"))
    graph.add_node("convert_cvs",        _safe(convert_cvs,        "convert_cvs"))
    graph.add_node("generate_queries",   _safe(generate_queries,   "generate_queries"))
    graph.add_node("search_jobs",        _safe(search_jobs,        "search_jobs"))
    graph.add_node("search_companies",   _safe(search_companies,   "search_companies"))
    graph.add_node("aggregate_jobs",     _safe(aggregate_jobs,     "aggregate_jobs"))
    graph.add_node("analyze_jobs",       _safe(analyze_jobs,       "analyze_jobs"))
    graph.add_node("store_results",      _safe(store_results,      "store_results"))
    graph.add_node("send_notifications", _safe(send_notifications, "send_notifications"))

    graph.set_entry_point("load_context")

    # Conditional: PDFs → convert; otherwise skip straight to query generation
    graph.add_conditional_edges("load_context", _needs_convert_cvs, {
        "convert_cvs": "convert_cvs",
        "generate_queries": "generate_queries",
    })
    graph.add_edge("convert_cvs", "generate_queries")

    graph.add_edge("generate_queries", "search_jobs")

    # Linear core pipeline
    graph.add_edge("search_jobs", "search_companies")
    graph.add_edge("search_companies", "aggregate_jobs")
    graph.add_edge("aggregate_jobs", "analyze_jobs")
    graph.add_edge("analyze_jobs", "store_results")

    # Conditional: only notify if channels are configured
    graph.add_conditional_edges("store_results", _needs_notifications, {
        "send_notifications": "send_notifications",
        END: END,
    })

    graph.add_edge("send_notifications", END)

    return graph.compile()
