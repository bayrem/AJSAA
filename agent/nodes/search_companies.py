"""Search career pages for companies in query/company_list.md.

Routing priority per company:
  1. hints_cache.json entry → ATS connector or URL-scoped web search
  2. No entry → discover career page URL via one LLM call, persist to cache
  3. hint == "none" → skip (discovery failed previously)
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from agent.state import AgentState

logger = logging.getLogger(__name__)

HINTS_CACHE_FILE = Path("query/hints_cache.json")

_LOCATION_KEYWORDS = ["paris", "france", "remote", "télétravail", "hybrid", "île-de-france"]

DISCOVER_PROMPT = """What is the careers/jobs page URL for {company}?
Return only the URL (e.g. https://jobs.example.com), nothing else.
If you are not confident, return the word UNKNOWN."""

SEARCH_PROMPT = """Today is {today}. Search {scope} for open job positions at {company}.
Only include jobs posted in the last {recency_days} days.
Focus on roles matching these profiles: {cv_titles}

Return a JSON array where each item has:
- title: job title
- company: company name
- location: city / country
- url: direct link to the posting
- description: 2-3 sentence summary
- posted_date: date posted as YYYY-MM-DD (omit if unknown)

Return [] if no relevant positions found. Return only the JSON array, nothing else."""


def _save_hint(company: str, hint: str) -> None:
    try:
        cache: dict = {}
        if HINTS_CACHE_FILE.exists():
            raw = json.loads(HINTS_CACHE_FILE.read_text(encoding="utf-8"))
            cache = {k: v for k, v in raw.items() if not k.startswith("_")}
        cache[company] = hint
        HINTS_CACHE_FILE.write_text(
            json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as e:
        logger.warning("Failed to persist hint for '%s': %s", company, e)


def _discover_url(company: str, llm) -> str:
    """Ask the LLM for the company's career page URL. Returns 'none' on failure."""
    try:
        from langchain_core.messages import HumanMessage
        response = llm.invoke([HumanMessage(content=DISCOVER_PROMPT.format(company=company))])
        url = response.content.strip().rstrip(".")
        if url.startswith("http") and "." in url:
            return f"url:{url}"
    except Exception as e:
        logger.warning("URL discovery failed for '%s': %s", company, e)
    return "none"


def _ats_connector(ats_name: str, cfg: dict):
    """Return the appropriate ATS connector instance, or None if unknown."""
    if ats_name == "greenhouse":
        from providers.search.connectors.greenhouse import GreenhouseConnector
        return GreenhouseConnector(cfg)
    if ats_name == "lever":
        from providers.search.connectors.lever import LeverConnector
        return LeverConnector(cfg)
    if ats_name == "ashby":
        from providers.search.connectors.ashby import AshbyConnector
        return AshbyConnector(cfg)
    return None


def _search_with_hint(company: str, hint: str, llm, cfg: dict, cv_titles: str) -> list[dict]:
    """Execute search for a company given its routing hint."""
    if hint.startswith(("greenhouse:", "lever:", "ashby:")):
        ats_name, slug = hint.split(":", 1)
        connector = _ats_connector(ats_name, cfg.get("search", {}))
        if connector is None:
            return []
        results = connector.fetch(slug, location_keywords=_LOCATION_KEYWORDS)
        for job in results:
            job.setdefault("company", company)
        return results

    if not hint.startswith("url:"):
        return []

    from providers.search.web_search import AnthropicWebSearchProvider

    scope = hint[4:]
    recency_days = cfg.get("search", {}).get("recency_days", 3)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    provider = AnthropicWebSearchProvider(llm, cfg.get("search", {}))
    prompt = SEARCH_PROMPT.format(
        today=today,
        scope=scope,
        company=company,
        recency_days=recency_days,
        cv_titles=cv_titles,
    )
    results = provider.search_with_prompt(prompt, max_results=5)
    for job in results:
        job.setdefault("company", company)
    return results


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
    hints: dict = state.get("company_hints", {})
    cv_titles = ", ".join(cv["name"].replace("_", " ") for cv in cvs) or "product management, AI, data"

    try:
        from providers.llm.factory import build_llm
        llm = build_llm(cfg["llm"])
    except Exception as e:
        errors.append(f"Company search initialisation failed: {e}")
        logger.error("Company search init failed: %s", e)
        return {**state, "raw_jobs": raw_jobs, "errors": errors, "run_log": run_log}

    for company in companies:
        try:
            hint = hints.get(company)

            if hint is None:
                logger.info("[companies] '%s' — no hint, discovering career page...", company)
                hint = _discover_url(company, llm)
                _save_hint(company, hint)
                hints[company] = hint
                run_log.append(f"[companies] '{company}' discovered hint: {hint}")

            if hint == "none":
                run_log.append(f"[companies] '{company}' skipped (hint=none)")
                logger.info("[companies] '%s' skipped (hint=none)", company)
                continue

            results = _search_with_hint(company, hint, llm, cfg, cv_titles)
            raw_jobs.extend(results)
            run_log.append(f"[companies] '{company}' → {len(results)} results")
            logger.info("[companies] '%s' → %d results", company, len(results))

        except Exception as e:
            errors.append(f"Company search failed for '{company}': {e}")
            logger.error("Company search failed for '%s': %s", company, e)

    return {**state, "raw_jobs": raw_jobs, "company_hints": hints, "errors": errors, "run_log": run_log}
