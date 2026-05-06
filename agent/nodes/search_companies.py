"""Search career pages for companies listed in query/company_list.md."""
import logging

from agent.state import AgentState

logger = logging.getLogger(__name__)

PROMPT = """Search for open job positions at {company}. Focus on roles that match these profiles:
{cv_titles}

Return a JSON array where each item has: title, company, location, url, description.
Return [] if no relevant positions found. Return only the JSON array, nothing else."""


def run(state: AgentState) -> AgentState:
    errors = list(state.get("errors", []))
    run_log = list(state.get("run_log", []))
    raw_jobs = list(state.get("raw_jobs", []))

    companies = state.get("companies", [])
    if not companies:
        run_log.append("No companies in list — skipping company search")
        return {**state, "raw_jobs": raw_jobs, "errors": errors, "run_log": run_log}

    if not state["config"].get("search", {}).get("enable_company_pages", True):
        run_log.append("Company page search disabled in config")
        return {**state, "raw_jobs": raw_jobs, "errors": errors, "run_log": run_log}

    cfg = state["config"]
    cvs = state.get("cvs", [])
    cv_titles = ", ".join(cv["name"].replace("_", " ") for cv in cvs) or "product management, AI, data"

    try:
        from providers.llm.factory import build_llm
        from providers.search.web_search import AnthropicWebSearchProvider

        llm = build_llm(cfg["llm"])
        search_provider = AnthropicWebSearchProvider(llm, cfg.get("search", {}))

        for company in companies:
            try:
                query = f"{company} careers open positions jobs"
                results = search_provider.search(query, max_results=5, context=cv_titles)
                for job in results:
                    job.setdefault("company", company)
                raw_jobs.extend(results)
                run_log.append(f"[companies] '{company}' → {len(results)} results")
                logger.info("[companies] '%s' → %d results", company, len(results))
            except Exception as e:
                errors.append(f"Company search failed for '{company}': {e}")
                logger.error("Company search failed for '%s': %s", company, e)

    except Exception as e:
        errors.append(f"Company search initialisation failed: {e}")
        logger.error("Company search init failed: %s", e)

    return {**state, "raw_jobs": raw_jobs, "errors": errors, "run_log": run_log}
