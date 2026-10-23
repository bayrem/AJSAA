"""Search spoke tools — turn URLs and ATS boards into validated job candidates.

Every job enters the run store through :func:`save_candidates`, which merges
with what earlier search rounds already found, so the orchestrator can search
again (new queries, new sources) without creating duplicates.

There are two ways in:
  - :func:`validate_urls` — for URLs found by web search or by job-board MCP
    servers. Tavily fetches the real page; unreachable or hallucinated URLs
    are dropped. This is the single anti-hallucination gate.
  - :func:`ats_search` — Greenhouse / Lever / Ashby public APIs. Structured
    data straight from the source, zero LLM tokens.
"""
import logging
from collections import Counter

# TODO(M2): the agent.nodes helpers move here when agent/nodes/ is deleted.
from agent.nodes.aggregate_jobs import MAX_JOBS, _dedup_by_url
from agent.nodes.load_context import _load_hints_cache, _parse_companies
from agent.nodes.search_companies import _LOCATION_KEYWORDS, MAX_RESULTS_PER_COMPANY
from agent.nodes.search_jobs import _filter_recent, _make_job_id
from agent.run_store import CANDIDATES, RunStore
from providers.search.dedup import semantic_deduplicate

logger = logging.getLogger(__name__)

_ATS_PREFIXES = ("greenhouse:", "lever:", "ashby:")


def save_candidates(store: RunStore, new_jobs: list[dict], max_jobs: int = MAX_JOBS) -> str:
    """Merge ``new_jobs`` into the run's candidates table and return a summary.

    Pipeline (same rules as the v2 search + aggregate nodes): drop stale
    postings → assign stable ``job_id`` → dedupe by URL and by id → semantic
    dedupe (same role, different URL) → cap at ``max_jobs``. Existing
    candidates come first, so earlier rounds keep their place under the cap.
    """
    existing = store.read_jsonl(CANDIDATES)
    fresh = _filter_recent(new_jobs)
    for job in fresh:
        job["job_id"] = job.get("job_id") or _make_job_id(job)

    merged = _dedup_by_url(existing + fresh)
    seen_ids: set[str] = set()
    by_id: list[dict] = []
    for job in merged:
        if job["job_id"] not in seen_ids:
            seen_ids.add(job["job_id"])
            by_id.append(job)
    unique = semantic_deduplicate(by_id)[:max_jobs]

    store.write_jsonl(CANDIDATES, unique)
    added = len(unique) - len(existing)
    stale = len(new_jobs) - len(fresh)
    return (
        f"{added} new candidates saved ({len(new_jobs)} received, {stale} stale, "
        f"{len(new_jobs) - stale - added} duplicates or over cap). Total candidates: {len(unique)}/{max_jobs}."
    )


def validate_urls(store: RunStore, search_cfg: dict, candidates: list[dict]) -> str:
    """Validate URL candidates via Tavily extract and save the real postings.

    Args:
        candidates: ``[{"url": str, "source": str, "found_in_snippet": str}]``
            — only ``url`` is required.
    """
    from providers.search.url_validator import validate_and_enrich

    valid = [c for c in candidates if isinstance(c, dict) and c.get("url")]
    jobs = validate_and_enrich(valid, search_cfg, max_results=MAX_JOBS)
    by_source = Counter(j.get("source", "other") for j in jobs)
    sources = ", ".join(f"{s}: {n}" for s, n in by_source.most_common()) or "none"
    summary = save_candidates(store, jobs)
    return f"{len(jobs)}/{len(valid)} URLs validated by Tavily ({sources}). {summary}"


def load_company_hints(cfg: dict) -> dict[str, str]:
    """Return ``{company: hint}`` for configured companies (config hints win over cache)."""
    names, inline_hints = _parse_companies(cfg.get("companies", []))
    cache = _load_hints_cache()
    hints = {name: cache[name] for name in names if name in cache}
    hints.update(inline_hints)
    return hints


def ats_search(store: RunStore, search_cfg: dict, company_hints: dict[str, str]) -> str:
    """Fetch open jobs from company ATS boards and save them as candidates.

    Args:
        company_hints: ``{company: "greenhouse:slug" | "lever:slug" | "ashby:slug"}``.
            Non-ATS hints (``url:…``, ``none``) are reported as skipped — those
            companies are left to web search.
    """
    from providers.search.connectors.ats import build_ats_connector

    jobs: list[dict] = []
    lines: list[str] = []
    for company, hint in company_hints.items():
        if not hint.startswith(_ATS_PREFIXES):
            lines.append(f"{company}: skipped (no ATS hint)")
            continue
        ats_name, slug = hint.split(":", 1)
        connector = build_ats_connector(ats_name, search_cfg)
        if connector is None:
            lines.append(f"{company}: unknown ATS '{ats_name}'")
            continue
        try:
            results = connector.fetch(slug, location_keywords=_LOCATION_KEYWORDS)[:MAX_RESULTS_PER_COMPANY]
        except Exception as e:
            # One broken board must not sink the others.
            logger.warning("ats_search: %s (%s) failed: %s", company, hint, e)
            lines.append(f"{company}: failed ({e})")
            continue
        for job in results:
            job.setdefault("company", company)
        jobs.extend(results)
        lines.append(f"{company}: {len(results)} jobs")

    summary = save_candidates(store, jobs)
    return "ATS boards — " + "; ".join(lines or ["no companies given"]) + f". {summary}"
