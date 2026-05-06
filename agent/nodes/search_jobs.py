"""Search for jobs using configured providers and connectors."""
import logging

from agent.state import AgentState

logger = logging.getLogger(__name__)


def _parse_connector_cfg(entry) -> dict:
    """Normalise a connector entry to a dict regardless of whether it's a str or dict.

    Supported config shapes:
      - "france_travail"                         (string — all defaults)
      - {name: france_travail, enabled: false}   (explicit disable)
      - {name: anthropic_web, fallback_only: true, max_results_per_query: 5}
    """
    if isinstance(entry, str):
        return {"name": entry, "enabled": True, "fallback_only": False}
    return {
        "name": entry.get("name", ""),
        "enabled": entry.get("enabled", True),
        "fallback_only": entry.get("fallback_only", False),
        "max_results_per_query": entry.get("max_results_per_query"),  # None = use global
        "max_queries": entry.get("max_queries"),                      # None = all queries
    }


def run(state: AgentState) -> AgentState:
    errors = list(state.get("errors", []))
    run_log = list(state.get("run_log", []))
    raw_jobs = list(state.get("raw_jobs", []))

    queries = state.get("queries") or state.get("raw_queries", [])
    if not queries:
        run_log.append("No queries available — skipping job search")
        return {**state, "raw_jobs": raw_jobs, "errors": errors, "run_log": run_log}

    cfg = state["config"]
    search_cfg = cfg.get("search", {})
    global_max_results = search_cfg.get("max_results_per_query", 10)
    raw_connectors = search_cfg.get("connectors", ["anthropic_web"])

    connectors = [_parse_connector_cfg(c) for c in raw_connectors]
    enabled = [c for c in connectors if c["enabled"]]
    primary = [c for c in enabled if not c["fallback_only"]]
    fallbacks = [c for c in enabled if c["fallback_only"]]

    from providers.llm.factory import build_llm
    llm = build_llm(cfg["llm"])

    def _run_connector(connector_cfg: dict, query_list: list[str]) -> list[dict]:
        name = connector_cfg["name"]
        max_results = connector_cfg.get("max_results_per_query") or global_max_results
        max_q = connector_cfg.get("max_queries")
        scoped_queries = query_list[:max_q] if max_q else query_list
        found = []
        try:
            provider = _get_search_provider(name, llm, search_cfg)
            for query in scoped_queries:
                recent_query = f"{query} posted last week"
                try:
                    results = provider.search(recent_query, max_results=max_results)
                    found.extend(results)
                    run_log.append(f"[{name}] '{query}' → {len(results)} results")
                    logger.info("[%s] '%s' → %d results", name, query, len(results))
                except Exception as e:
                    errors.append(f"Search failed [{name}] query='{query}': {e}")
                    logger.error("Search failed [%s] query='%s': %s", name, query, e)
        except Exception as e:
            errors.append(f"Failed to initialise connector '{name}': {e}")
            logger.error("Failed to init connector '%s': %s", name, e)
        return found

    # Run primary connectors
    for c in primary:
        raw_jobs.extend(_run_connector(c, queries))

    # Run fallback connectors only when primary connectors returned nothing
    if fallbacks:
        if raw_jobs:
            skipped = [c["name"] for c in fallbacks]
            run_log.append(f"Fallback connectors skipped (primary found results): {skipped}")
            logger.info("Fallback connectors skipped: %s", skipped)
        else:
            run_log.append("Primary connectors returned 0 results — activating fallbacks")
            for c in fallbacks:
                raw_jobs.extend(_run_connector(c, queries))

    # Filter stale jobs
    raw_jobs = _filter_recent(raw_jobs)

    # Deduplicate by job_id
    seen = set()
    deduped = []
    for job in raw_jobs:
        jid = job.get("job_id") or _make_job_id(job)
        job["job_id"] = jid
        if jid not in seen:
            seen.add(jid)
            deduped.append(job)

    run_log.append(f"Job search complete: {len(deduped)} unique jobs found")
    logger.info("Job search complete: %d unique jobs", len(deduped))

    return {**state, "raw_jobs": deduped, "errors": errors, "run_log": run_log}


def _get_search_provider(name: str, llm, cfg: dict):
    if name == "anthropic_web":
        from providers.search.web_search import AnthropicWebSearchProvider
        return AnthropicWebSearchProvider(llm, cfg)
    elif name == "apec":
        from providers.search.connectors.apec import APECConnector
        return APECConnector(cfg)
    elif name == "linkedin":
        from providers.search.connectors.linkedin import LinkedInConnector
        return LinkedInConnector(cfg)
    elif name == "indeed":
        from providers.search.connectors.indeed import IndeedConnector
        return IndeedConnector(cfg)
    elif name == "wttj":
        from providers.search.connectors.wttj import WTTJConnector
        return WTTJConnector(cfg)
    elif name == "france_travail":
        from providers.search.connectors.france_travail import FranceTravailConnector
        return FranceTravailConnector(cfg)
    elif name == "adzuna":
        from providers.search.connectors.adzuna import AdzunaConnector
        return AdzunaConnector(cfg)
    else:
        raise ValueError(f"Unknown search connector: {name}")


_STALE_SIGNALS = [
    "posted last month", "posted 2 months", "posted 3 months",
    "il y a 1 mois", "il y a 2 mois", "il y a 3 mois",
    "30+ days ago", "more than 30 days",
]


def _filter_recent(jobs: list[dict]) -> list[dict]:
    kept = []
    removed = 0
    for job in jobs:
        text = (job.get("description", "") + " " + job.get("title", "")).lower()
        if any(s in text for s in _STALE_SIGNALS):
            removed += 1
            continue
        kept.append(job)
    if removed:
        logger.info("Date filter removed %d stale jobs", removed)
    return kept


def _make_job_id(job: dict) -> str:
    import hashlib
    key = f"{job.get('title', '')}|{job.get('company', '')}|{job.get('location', '')}".lower()
    return hashlib.sha256(key.encode()).hexdigest()[:16]
