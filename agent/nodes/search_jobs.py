"""Search for jobs using configured providers and connectors."""
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Semaphore

from agent.state import AgentState
from providers.resilience import circuit_breaker

logger = logging.getLogger(__name__)

# Default per-connector concurrency limits (overridable via max_concurrent in config.yaml)
_DEFAULT_MAX_CONCURRENT = {
    "france_travail": 3,   # respects their 3 req/s rate limit
    "adzuna": 5,            # no documented limit; conservative default
    "anthropic_web": 1,     # LLM-backed — parallelism yields no wall-clock benefit
}
_FALLBACK_MAX_CONCURRENT = 3


def _parse_connector_cfg(entry) -> dict:
    """Normalise a connector entry to a dict regardless of whether it's a str or dict.

    Supported config shapes:
      - "france_travail"                         (string — all defaults)
      - {name: france_travail, enabled: false}   (explicit disable)
      - {name: anthropic_web, fallback_only: true, max_results_per_query: 5}
    """
    if isinstance(entry, str):
        return {"name": entry, "enabled": True, "fallback_only": False, "max_concurrent": None}
    return {
        "name": entry.get("name", ""),
        "enabled": entry.get("enabled", True),
        "fallback_only": entry.get("fallback_only", False),
        "max_results_per_query": entry.get("max_results_per_query"),  # None = use global
        "max_queries": entry.get("max_queries"),                      # None = all queries
        "max_concurrent": entry.get("max_concurrent"),                # None = use default
    }


def _search_one(provider, connector_name: str, query: str, max_results: int,
                semaphore: Semaphore, recency_days: int = 3):
    """Execute one (connector, query) pair under the connector's semaphore.

    Returns (results, log_message, error_message) — error_message is None on success.
    """
    recent_query = f"{query} last {recency_days} days"
    with semaphore:
        try:
            results = provider.search(recent_query, max_results=max_results)
            circuit_breaker.record_success(connector_name)
            return results, f"[{connector_name}] '{query}' → {len(results)} results", None
        except Exception as e:
            circuit_breaker.record_failure(connector_name)
            return [], None, f"Search failed [{connector_name}] query='{query}': {e}"


def _run_parallel(connector_cfgs: list[dict], queries: list[str], llm, search_cfg: dict,
                  run_log: list, errors: list, recency_days: int = 3) -> list[dict]:
    """Run all (connector, query) pairs in parallel with per-connector concurrency limits."""
    global_max_results = search_cfg.get("max_results_per_query", 10)

    # Initialise one provider instance per connector (connectors must be stateless/thread-safe)
    providers: dict[str, object] = {}
    semaphores: dict[str, Semaphore] = {}
    for cfg in connector_cfgs:
        name = cfg["name"]
        if circuit_breaker.is_open(name):
            run_log.append(f"[circuit_breaker] '{name}' is open — skipped")
            continue
        try:
            providers[name] = _get_search_provider(name, llm, search_cfg)
            limit = cfg.get("max_concurrent") or _DEFAULT_MAX_CONCURRENT.get(name, _FALLBACK_MAX_CONCURRENT)
            semaphores[name] = Semaphore(limit)
        except Exception as e:
            errors.append(f"Failed to initialise connector '{name}': {e}")
            logger.error("Failed to init connector '%s': %s", name, e)

    # Build task list: one entry per (connector, query) pair
    tasks: list[tuple] = []
    for cfg in connector_cfgs:
        name = cfg["name"]
        if name not in providers:
            continue
        max_results = cfg.get("max_results_per_query") or global_max_results
        max_q = cfg.get("max_queries")
        scoped_queries = queries[:max_q] if max_q else queries
        for query in scoped_queries:
            tasks.append((providers[name], name, query, max_results, semaphores[name], recency_days))

    if not tasks:
        return []

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=min(len(tasks), 16)) as pool:
        futures = {
            pool.submit(_search_one, provider, name, query, max_results, sem, recency_days): (name, query)
            for provider, name, query, max_results, sem, recency_days in tasks
        }
        for future in as_completed(futures):
            connector_name, query = futures[future]
            try:
                job_results, log, error = future.result()
                if log:
                    run_log.append(log)
                    logger.info(log)
                if error:
                    errors.append(error)
                    logger.error(error)
                results.extend(job_results)
            except Exception as e:
                errors.append(f"Unexpected error [{connector_name}] query='{query}': {e}")
                logger.error("Unexpected error [%s] query='%s': %s", connector_name, query, e)

    return results


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
    raw_connectors = search_cfg.get("connectors", ["anthropic_web"])

    connectors = [_parse_connector_cfg(c) for c in raw_connectors]
    enabled = [c for c in connectors if c["enabled"]]
    primary = [c for c in enabled if not c["fallback_only"]]
    fallbacks = [c for c in enabled if c["fallback_only"]]

    from providers.llm.factory import build_llm
    llm = build_llm(cfg["llm"], task="search")

    recency_days = search_cfg.get("recency_days", 3)

    # Run primary connectors in parallel
    raw_jobs.extend(_run_parallel(primary, queries, llm, search_cfg, run_log, errors, recency_days))

    # Run fallback connectors only when primary connectors returned nothing
    if fallbacks:
        if raw_jobs:
            skipped = [c["name"] for c in fallbacks]
            run_log.append(f"Fallback connectors skipped (primary found results): {skipped}")
            logger.info("Fallback connectors skipped: %s", skipped)
        else:
            run_log.append("Primary connectors returned 0 results — activating fallbacks")
            raw_jobs.extend(_run_parallel(fallbacks, queries, llm, search_cfg, run_log, errors, recency_days))

    # Filter stale jobs
    raw_jobs = _filter_recent(raw_jobs)

    # Deduplicate by job_id
    seen: set[str] = set()
    deduped: list[dict] = []
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
    if name == "adaptive_web":
        from providers.search.connectors.adaptive_web import AdaptiveWebSearchProvider
        return AdaptiveWebSearchProvider(llm, cfg)
    elif name == "anthropic_web":
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
