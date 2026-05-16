"""Generate job-search queries from CV content when none are provided.

This node only runs if ``raw_queries`` is empty — i.e. the user has not
provided ``query/job_queries.md`` and we need to bootstrap queries by asking
the LLM to read the CVs and suggest searches.

Output queries are written to ``state["queries"]`` so the downstream
``search_jobs`` node has a uniform key to read from regardless of source.
"""
import json
import logging

from agent.state import AgentState
from providers.utils import strip_json_fence

logger = logging.getLogger(__name__)


# Prompt asks for a *short* list of broad queries. The agent's parallel-search
# layer multiplies these across multiple connectors, so 5-10 queries is
# typically enough to cover the space without exploding API costs.
PROMPT = """You are a job search expert. Based on the CV profiles below, generate {n} specific job search queries to find relevant positions.

Rules:
- Each query should be a short search string (e.g., "AI Product Manager Paris")
- Include job title + location when relevant
- Focus on roles where the candidate's skills are a strong match
- Return a JSON array of strings, nothing else

CV Profiles:
{cvs}

Return format: ["query 1", "query 2", ...]"""


def run(state: AgentState) -> AgentState:
    # If queries already exist from job_queries.md, skip generation entirely.
    # We treat the file as authoritative when present.
    if state.get("raw_queries"):
        return {**state, "queries": state["raw_queries"]}

    errors = list(state.get("errors", []))
    run_log = list(state.get("run_log", []))

    if not state.get("cvs"):
        errors.append("Cannot generate queries: no CVs loaded")
        return {**state, "queries": [], "errors": errors, "run_log": run_log}

    queries: list[str] = []
    try:
        from langchain_core.messages import HumanMessage

        from providers.llm.factory import build_llm

        # Use the cheap "search" model — query generation is a simple task.
        llm = build_llm(state["config"]["llm"], task="search")

        # Truncate each CV to 2000 chars to keep the prompt size predictable.
        cv_summaries = "\n\n---\n\n".join(
            f"[{cv['name']}]\n{cv['content'][:2000]}" for cv in state["cvs"]
        )
        # Cap at 10 — beyond that the searches start overlapping anyway.
        n_queries = state["config"].get("search", {}).get("max_results_per_query", 5)
        prompt = PROMPT.format(n=min(n_queries, 10), cvs=cv_summaries)

        response = llm.invoke([HumanMessage(content=prompt)])
        raw = strip_json_fence(response.content.strip())

        queries = json.loads(raw)
        if not isinstance(queries, list):
            raise ValueError("LLM did not return a list")

        # Filter out empty strings / non-strings — defensive coding against
        # an LLM that returns ``[null, "Real query"]`` etc.
        queries = [q for q in queries if isinstance(q, str) and q.strip()]
        run_log.append(f"LLM generated {len(queries)} queries from CVs")
        logger.info("Generated %d queries from CVs", len(queries))

    except Exception as e:
        errors.append(f"Query generation failed: {e}")
        logger.error("Query generation failed: %s", e)
        queries = []

    return {**state, "queries": queries, "errors": errors, "run_log": run_log}
