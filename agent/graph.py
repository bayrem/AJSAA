from langgraph.graph import StateGraph, END

from agent.state import AgentState
from agent.nodes.load_context import run as load_context
from agent.nodes.convert_cvs import run as convert_cvs
from agent.nodes.generate_queries import run as generate_queries
from agent.nodes.search_jobs import run as search_jobs
from agent.nodes.search_companies import run as search_companies
from agent.nodes.analyze_jobs import run as analyze_jobs
from agent.nodes.store_results import run as store_results
from agent.nodes.send_notifications import run as send_notifications


def _needs_convert_cvs(state: AgentState) -> str:
    return "convert_cvs" if state["pdf_paths"] else "generate_queries"


def _needs_generate_queries(state: AgentState) -> str:
    return "generate_queries" if not state["raw_queries"] else "search_jobs"


def _needs_notifications(state: AgentState) -> str:
    cfg = state["config"]
    if cfg.get("notifications", {}).get("enabled") and cfg["notifications"].get("channels"):
        return "send_notifications"
    return END


def build_graph() -> StateGraph:
    graph = StateGraph(AgentState)

    graph.add_node("load_context", load_context)
    graph.add_node("convert_cvs", convert_cvs)
    graph.add_node("generate_queries", generate_queries)
    graph.add_node("search_jobs", search_jobs)
    graph.add_node("search_companies", search_companies)
    graph.add_node("analyze_jobs", analyze_jobs)
    graph.add_node("store_results", store_results)
    graph.add_node("send_notifications", send_notifications)

    graph.set_entry_point("load_context")

    graph.add_conditional_edges("load_context", _needs_convert_cvs, {
        "convert_cvs": "convert_cvs",
        "generate_queries": "generate_queries",
    })

    graph.add_edge("convert_cvs", "generate_queries")

    graph.add_conditional_edges("generate_queries", _needs_generate_queries, {
        "generate_queries": "generate_queries",
        "search_jobs": "search_jobs",
    })

    graph.add_edge("search_jobs", "search_companies")
    graph.add_edge("search_companies", "analyze_jobs")
    graph.add_edge("analyze_jobs", "store_results")

    graph.add_conditional_edges("store_results", _needs_notifications, {
        "send_notifications": "send_notifications",
        END: END,
    })

    graph.add_edge("send_notifications", END)

    return graph.compile()
