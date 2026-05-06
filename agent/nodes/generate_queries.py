"""Generate job search queries from CV content when none are provided."""
import json
import logging

from agent.state import AgentState

logger = logging.getLogger(__name__)

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
    # If queries already exist from file, skip generation
    if state.get("raw_queries"):
        return {**state, "queries": state["raw_queries"]}

    errors = list(state.get("errors", []))
    run_log = list(state.get("run_log", []))

    if not state.get("cvs"):
        errors.append("Cannot generate queries: no CVs loaded")
        return {**state, "queries": [], "errors": errors, "run_log": run_log}

    try:
        from providers.llm.factory import build_llm
        from langchain_core.messages import HumanMessage

        llm = build_llm(state["config"]["llm"])

        cv_summaries = "\n\n---\n\n".join(
            f"[{cv['name']}]\n{cv['content'][:2000]}" for cv in state["cvs"]
        )
        n_queries = state["config"].get("search", {}).get("max_results_per_query", 5)
        prompt = PROMPT.format(n=min(n_queries, 10), cvs=cv_summaries)

        response = llm.invoke([HumanMessage(content=prompt)])
        raw = response.content.strip()

        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        queries = json.loads(raw)
        if not isinstance(queries, list):
            raise ValueError("LLM did not return a list")

        queries = [q for q in queries if isinstance(q, str) and q.strip()]
        run_log.append(f"LLM generated {len(queries)} queries from CVs")
        logger.info("Generated %d queries from CVs", len(queries))

    except Exception as e:
        errors.append(f"Query generation failed: {e}")
        logger.error("Query generation failed: %s", e)
        queries = []

    return {**state, "queries": queries, "errors": errors, "run_log": run_log}
