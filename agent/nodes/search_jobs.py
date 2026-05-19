"""Job-search node — runs every configured connector against every query.

Connectors run in parallel via a ``ThreadPoolExecutor``, with one
:class:`Semaphore` per connector to enforce that connector's individual
rate-limit. Primary connectors run first; fallback connectors run *only* if
no primary returned any results.

Flow:

  1. Parse the config-level connector list (mix of strings and dicts).
  2. Filter out connectors whose circuit breaker is open.
  3. Build the full set of (connector, query[, board]) tasks.
  4. Run them in parallel; aggregate results.
  5. If primaries returned nothing, repeat with the fallback set.
  6. Filter stale postings, deduplicate, return.

Public/test API:
  - ``run(state)`` — graph node entrypoint.
  - ``_parse_connector_cfg``, ``_search_one``, ``_run_parallel``,
    ``_filter_recent``, ``_make_job_id``, ``_get_search_provider`` are
    imported directly by tests and must keep their names.
"""
import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Semaphore
from typing import Callable

from agent.state import AgentState
from providers.resilience import circuit_breaker

logger = logging.getLogger(__name__)


# ── Concurrency defaults ─────────────────────────────────────────────────────

# Per-connector defaults for max concurrent in-flight requests. Tuned to each
# connector's documented or empirically-safe rate limits. Override per
# connector via ``max_concurrent`` in config.yaml.
_DEFAULT_MAX_CONCURRENT = {
    "france_travail": 3,    # Documented 3 req/s ceiling
    "adzuna": 5,            # No documented limit; conservative default
    "anthropic_web": 1,     # LLM-backed — parallelism yields nothing
}
_FALLBACK_MAX_CONCURRENT = 3


# Phrases that indicate a job posting is stale despite passing our recency
# filter at the API level. Some boards return month-old postings with newer
# "last touched" timestamps; this catches them by inspecting the body text.
_STALE_SIGNALS = [
    "posted last month", "posted 2 months", "posted 3 months",
    "il y a 1 mois", "il y a 2 mois", "il y a 3 mois",
    "30+ days ago", "more than 30 days",
]


# ── Connector registry ───────────────────────────────────────────────────────

# Each entry returns a fully-built search provider given (name, llm, cfg).
# Lambdas keep imports lazy so unused connectors don't load their
# dependencies (and don't fail if optional deps like tavily aren't installed).
def _get_search_provider(name: str, llm, cfg: dict):
    """Return a search-provider instance for the given connector name.

    Tests patch this function directly; keep the name stable.

    Raises:
        ValueError: If ``name`` is not a known connector.
    """
    builders: dict[str, Callable[[], object]] = {
        "anthropic_web": lambda: _make_anthropic_web(llm, cfg),
        "apec": lambda: _make_apec(cfg),
        "linkedin": lambda: _make_linkedin(cfg),
        "indeed": lambda: _make_indeed(cfg),
        "wttj": lambda: _make_wttj(cfg),
        "france_travail": lambda: _make_france_travail(cfg),
        "adzuna": lambda: _make_adzuna(cfg),
    }
    builder = builders.get(name)
    if builder is None:
        raise ValueError(f"Unknown search connector: {name}")
    return builder()


# Lazy import wrappers — pulled out into named functions so the dispatch
# table stays readable and each connector pays its own import cost only when
# actually instantiated.

def _make_anthropic_web(llm, cfg):
    from providers.search.web_search import AnthropicWebSearchProvider
    return AnthropicWebSearchProvider(llm, cfg)


def _make_apec(cfg):
    from providers.search.connectors.apec import APECConnector
    return APECConnector(cfg)


def _make_linkedin(cfg):
    from providers.search.connectors.linkedin import LinkedInConnector
    return LinkedInConnector(cfg)


def _make_indeed(cfg):
    from providers.search.connectors.indeed import IndeedConnector
    return IndeedConnector(cfg)


def _make_wttj(cfg):
    from providers.search.connectors.wttj import WTTJConnector
    return WTTJConnector(cfg)


def _make_france_travail(cfg):
    from providers.search.connectors.france_travail import FranceTravailConnector
    return FranceTravailConnector(cfg)


def _make_adzuna(cfg):
    from providers.search.connectors.adzuna import AdzunaConnector
    return AdzunaConnector(cfg)


# ── Connector config normalisation ───────────────────────────────────────────

def _parse_connector_cfg(entry) -> dict:
    """Normalise a single connector entry from config.yaml.

    Accepts either a bare string or a fully-spelled-out dict. Returns a dict
    populated with sensible defaults so downstream code can always use
    ``.get(...)`` without branching on the shape.

    Supported shapes::

        "france_travail"                                  # all defaults
        {name: france_travail, enabled: false}            # disabled
        {name: anthropic_web, fallback_only: true,
         max_results_per_query: 5}
    """
    if isinstance(entry, str):
        return {
            "name": entry,
            "enabled": True,
            "fallback_only": False,
            "max_concurrent": None,
        }
    return {
        "name": entry.get("name", ""),
        "enabled": entry.get("enabled", True),
        "fallback_only": entry.get("fallback_only", False),
        # ``None`` here means "use the global default" — distinguish from 0.
        "max_results_per_query": entry.get("max_results_per_query"),
        "max_queries": entry.get("max_queries"),
        "max_concurrent": entry.get("max_concurrent"),
        "target_boards": entry.get("target_boards"),
        "max_queries_per_board": entry.get("max_queries_per_board"),
    }


# ── Worker primitive ─────────────────────────────────────────────────────────

def _search_one(
    provider,
    connector_name: str,
    query: str,
    max_results: int,
    semaphore: Semaphore,
    recency_days: int = 3,
    board: str | None = None,
):
    """Execute one (connector, query[, board]) task under the connector's semaphore.

    Returns a tuple ``(results, log_message, error_message)``. Exactly one of
    ``log_message`` or ``error_message`` is non-None.
    """
    # Suffix the query with a recency hint. Anthropic-web uses this nudge;
    # API connectors strip it and apply their own date filter.
    recent_query = f"{query} last {recency_days} days"
    board_tag = f" [{board}]" if board else ""

    with semaphore:
        try:
            results = provider.search(recent_query, max_results=max_results, board=board)
            circuit_breaker.record_success(connector_name)
            return (
                results,
                f"[{connector_name}]{board_tag} '{query}' → {len(results)} results",
                None,
            )
        except Exception as e:
            circuit_breaker.record_failure(connector_name)
            return (
                [],
                None,
                f"Search failed [{connector_name}]{board_tag} query='{query}': {e}",
            )


# ── Parallel-execution orchestrator ──────────────────────────────────────────

def _init_connectors(
    connector_cfgs: list[dict],
    llm,
    search_cfg: dict,
    run_log: list,
    errors: list,
) -> tuple[dict[str, object], dict[str, Semaphore]]:
    """Instantiate one provider + semaphore per connector.

    Connectors whose circuit breaker is open are skipped here; failure to
    construct a provider is recorded to ``errors`` but does not raise.
    """
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

    return providers, semaphores


def _build_tasks(
    connector_cfgs: list[dict],
    providers: dict[str, object],
    semaphores: dict[str, Semaphore],
    queries: list[str],
    global_max_results: int,
    recency_days: int,
) -> list[tuple]:
    """Build the full set of (connector, query[, board]) tasks to execute.

    When a connector specifies ``target_boards``, each query is issued once
    per board with an optional per-board query cap. Without ``target_boards``
    the behaviour matches the simple "one task per query" model.
    """
    tasks: list[tuple] = []

    for cfg in connector_cfgs:
        name = cfg["name"]
        if name not in providers:
            continue
        max_results = cfg.get("max_results_per_query") or global_max_results
        max_q = cfg.get("max_queries")
        target_boards = cfg.get("target_boards") or []
        max_q_per_board = cfg.get("max_queries_per_board")

        if target_boards:
            # Per-board mode — emit one task per (query, board) pair, capping
            # the query count via the most restrictive of max_queries and
            # max_queries_per_board.
            effective_max_q = (
                min(max_q, max_q_per_board) if max_q and max_q_per_board
                else (max_q_per_board or max_q)
            )
            scoped_queries = queries[:effective_max_q] if effective_max_q else queries
            for board in target_boards:
                for query in scoped_queries:
                    tasks.append((
                        providers[name], name, query, max_results,
                        semaphores[name], recency_days, board,
                    ))
        else:
            # Simple mode — one task per query, no board filter.
            scoped_queries = queries[:max_q] if max_q else queries
            for query in scoped_queries:
                tasks.append((
                    providers[name], name, query, max_results,
                    semaphores[name], recency_days, None,
                ))

    return tasks


def _execute_tasks(tasks: list[tuple], run_log: list, errors: list) -> list[dict]:
    """Run all tasks through a thread pool, aggregate logs/errors/results."""
    if not tasks:
        return []

    results: list[dict] = []
    # 16 is an empirical ceiling; beyond it the LLM-bound and rate-limited
    # connectors stop benefiting from extra threads.
    with ThreadPoolExecutor(max_workers=min(len(tasks), 16)) as pool:
        futures = {
            pool.submit(_search_one, provider, name, query, max_results, sem, recency_days, board):
                (name, query)
            for provider, name, query, max_results, sem, recency_days, board in tasks
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
                # Defence against unexpected exceptions inside _search_one
                # that escape its try/except — should never happen but cheap
                # to guard against.
                errors.append(f"Unexpected error [{connector_name}] query='{query}': {e}")
                logger.error("Unexpected error [%s] query='%s': %s", connector_name, query, e)

    return results


def _run_parallel(
    connector_cfgs: list[dict],
    queries: list[str],
    llm,
    search_cfg: dict,
    run_log: list,
    errors: list,
    recency_days: int = 3,
) -> list[dict]:
    """Top-level coordinator: init connectors, build tasks, execute, return jobs."""
    global_max_results = search_cfg.get("max_results_per_query", 10)

    providers, semaphores = _init_connectors(
        connector_cfgs, llm, search_cfg, run_log, errors
    )
    tasks = _build_tasks(
        connector_cfgs, providers, semaphores, queries, global_max_results, recency_days
    )
    return _execute_tasks(tasks, run_log, errors)


# ── Post-processing ──────────────────────────────────────────────────────────

def _filter_recent(jobs: list[dict]) -> list[dict]:
    """Drop jobs whose title or description contains a "stale" phrase.

    Acts as a safety net after API-level recency filters — some boards return
    stale postings that were merely re-indexed recently.
    """
    kept: list[dict] = []
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
    """Generate a stable 16-char id from a job's identity fields.

    Used as a fallback when the connector didn't supply a ``job_id``. The
    same job from two different sources will hash to the same id so
    dedup-by-id removes cross-source duplicates.
    """
    key = f"{job.get('title', '')}|{job.get('company', '')}|{job.get('location', '')}".lower()
    return hashlib.sha256(key.encode()).hexdigest()[:16]


# ── Directive search (anthropic_web) ─────────────────────────────────────────

_DIRECTIVE_MAX_RESULTS = 30


def _run_directive_search(
    state: AgentState,
    llm,
    search_cfg: dict,
    run_log: list,
    errors: list,
) -> list[dict]:
    """One comprehensive search call for anthropic_web with full context.

    Replaces the N-query parallel loop for this connector — the LLM gets all
    positions, locations, and company hints in a single directive prompt and
    returns up to _DIRECTIVE_MAX_RESULTS results.
    """
    from providers.search.web_search import AnthropicWebSearchProvider

    cfg = state["config"]

    # Collect unique non-empty positions from the cvs config block
    cvs_cfg = cfg.get("search", {}).get("cvs", {})
    seen_positions: set[str] = set()
    positions: list[str] = []
    for titles in cvs_cfg.values():
        for t in (titles or []):
            if t and t.strip() and t.strip() not in seen_positions:
                seen_positions.add(t.strip())
                positions.append(t.strip())

    locations: list[str] = cfg.get("search", {}).get("locations", ["Paris"])
    companies: list[str] = state.get("companies", [])
    hints: dict = state.get("company_hints", {})

    run_log.append(
        f"[anthropic_web] directive search: {positions} × {locations}, "
        f"{len(companies)} companies, max {_DIRECTIVE_MAX_RESULTS}"
    )

    try:
        provider = AnthropicWebSearchProvider(llm, search_cfg)
        results = provider.search_all(
            positions=positions,
            locations=locations,
            companies=companies,
            hints=hints,
            max_results=_DIRECTIVE_MAX_RESULTS,
        )
        run_log.append(f"[anthropic_web] → {len(results)} results")
        logger.info("[anthropic_web] directive search → %d results", len(results))
        return results
    except Exception as e:
        errors.append(f"Directive search failed: {e}")
        logger.error("Directive search failed: %s", e)
        return []


# ── Graph node ───────────────────────────────────────────────────────────────

def run(state: AgentState) -> AgentState:
    """Search every configured connector × query combination and dedupe results."""
    errors = list(state.get("errors", []))
    run_log = list(state.get("run_log", []))
    raw_jobs = list(state.get("raw_jobs", []))

    # ``queries`` is the canonical key but older runs may carry ``raw_queries``
    # only — fall back to it so the node still produces output.
    queries = state.get("queries") or state.get("raw_queries", [])
    if not queries:
        run_log.append("No queries available — skipping job search")
        return {**state, "raw_jobs": raw_jobs, "errors": errors, "run_log": run_log}

    cfg = state["config"]
    search_cfg = cfg.get("search", {})
    raw_connectors = search_cfg.get("connectors", ["anthropic_web"])

    # Normalise to dicts so downstream code doesn't branch on type
    connectors = [_parse_connector_cfg(c) for c in raw_connectors]
    enabled = [c for c in connectors if c["enabled"]]
    primary = [c for c in enabled if not c["fallback_only"]]
    fallbacks = [c for c in enabled if c["fallback_only"]]

    # Lazy import to keep run.py's startup time tight when search isn't used
    from providers.llm.factory import build_llm
    llm = build_llm(cfg["llm"], task="search")

    recency_days = search_cfg.get("recency_days", 3)

    # anthropic_web gets one comprehensive directive call instead of N queries.
    # All other connectors (france_travail, adzuna, …) keep the parallel loop.
    directive_cfgs = [c for c in primary if c["name"] == "anthropic_web"]
    loop_primary = [c for c in primary if c["name"] != "anthropic_web"]
    directive_fallbacks = [c for c in fallbacks if c["name"] == "anthropic_web"]
    loop_fallbacks = [c for c in fallbacks if c["name"] != "anthropic_web"]

    if directive_cfgs:
        raw_jobs.extend(_run_directive_search(state, llm, search_cfg, run_log, errors))

    raw_jobs.extend(_run_parallel(loop_primary, queries, llm, search_cfg, run_log, errors, recency_days))

    # Fallback pass — only runs when primary produced nothing.
    if not raw_jobs:
        if directive_fallbacks:
            raw_jobs.extend(_run_directive_search(state, llm, search_cfg, run_log, errors))
        if loop_fallbacks:
            raw_jobs.extend(_run_parallel(loop_fallbacks, queries, llm, search_cfg, run_log, errors, recency_days))
    elif fallbacks:
        skipped = [c["name"] for c in fallbacks]
        run_log.append(f"Fallback connectors skipped (primary found results): {skipped}")
        logger.info("Fallback connectors skipped: %s", skipped)

    # Drop month-old postings that slipped past API recency filters
    raw_jobs = _filter_recent(raw_jobs)

    # Dedupe by job_id — same job from multiple sources collapses to one
    seen: set[str] = set()
    deduped: list[dict] = []
    for job in raw_jobs:
        jid = job.get("job_id") or _make_job_id(job)
        job["job_id"] = jid
        if jid not in seen:
            seen.add(jid)
            deduped.append(job)

    # Semantic pass — catches same role posted under different URLs/IDs
    from providers.search.dedup import semantic_deduplicate
    before_semantic = len(deduped)
    deduped = semantic_deduplicate(deduped)
    semantic_removed = before_semantic - len(deduped)
    if semantic_removed:
        run_log.append(f"Semantic dedup removed {semantic_removed} near-duplicate jobs")
        logger.info("Semantic dedup removed %d near-duplicates", semantic_removed)

    run_log.append(f"Job search complete: {len(deduped)} unique jobs found")
    logger.info("Job search complete: %d unique jobs", len(deduped))

    return {**state, "raw_jobs": deduped, "errors": errors, "run_log": run_log}
