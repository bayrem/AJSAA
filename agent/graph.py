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
    search_jobs ──> search_companies ──> analyze_jobs ──> store_results
                                                              │
                                                              ├──(notify enabled?)──> send_notifications
                                                              │
                                                              v
                                                             END
"""
import logging

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

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
        try:
            return node_fn(state)
        except Exception as exc:
            logger.error("Node '%s' crashed: %s", name, exc, exc_info=True)
            errors = list(state.get("errors", []))
            errors.append(f"Node '{name}' crashed: {exc}")
            return {**state, "errors": errors}
        finally:
            usage_tracker.set_node(None)

    # Preserve the node name so the dashboard's per-node lookup still works.
    wrapper.__name__ = name
    return wrapper


# ── Routing predicates ───────────────────────────────────────────────────────

def _needs_convert_cvs(state: AgentState) -> str:
    """Skip PDF conversion when no PDFs were queued."""
    return "convert_cvs" if state["pdf_paths"] else "generate_queries"


def _needs_generate_queries(state: AgentState) -> str:
    """Skip query generation when ``raw_queries`` already came from disk."""
    return "generate_queries" if not state["raw_queries"] else "search_jobs"


def _needs_notifications(state: AgentState) -> str:
    """Skip the notifications node when no channels are configured."""
    cfg = state["config"]
    if cfg.get("notifications", {}).get("enabled") and cfg["notifications"].get("channels"):
        return "send_notifications"
    return END


# ── Graph builder ────────────────────────────────────────────────────────────

def build_graph() -> CompiledStateGraph:
    """Construct and compile the AJSAA pipeline graph."""
    graph = StateGraph(AgentState)

    # Register every node wrapped in the safety net.
    graph.add_node("load_context",       _safe(load_context,       "load_context"))
    graph.add_node("convert_cvs",        _safe(convert_cvs,        "convert_cvs"))
    graph.add_node("generate_queries",   _safe(generate_queries,   "generate_queries"))
    graph.add_node("search_jobs",        _safe(search_jobs,        "search_jobs"))
    graph.add_node("search_companies",   _safe(search_companies,   "search_companies"))
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

    # Conditional: skip LLM query generation when queries already exist
    graph.add_conditional_edges("generate_queries", _needs_generate_queries, {
        "generate_queries": "generate_queries",
        "search_jobs": "search_jobs",
    })

    # Linear core pipeline
    graph.add_edge("search_jobs", "search_companies")
    graph.add_edge("search_companies", "analyze_jobs")
    graph.add_edge("analyze_jobs", "store_results")

    # Conditional: only notify if channels are configured
    graph.add_conditional_edges("store_results", _needs_notifications, {
        "send_notifications": "send_notifications",
        END: END,
    })

    graph.add_edge("send_notifications", END)

    return graph.compile()
