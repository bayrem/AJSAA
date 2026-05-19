"""Aggregate, deduplicate, and checkpoint all found jobs.

This node sits between the two search nodes (search_jobs, search_companies)
and the scoring node (analyze_jobs). It:

  1. Merges raw_jobs from both search passes.
  2. Deduplicates by URL (first occurrence wins).
  3. Caps at MAX_JOBS (50) — the scoring prompt receives the full set in one
     call, so this is both a cost guard and a quality gate (keep the freshest,
     most relevant results that appeared earlier in the search).
  4. Writes the deduplicated list to ``query/jobs_found.jsonl`` so there is
     a clean on-disk checkpoint that survives a crash and can be inspected
     independently of the run report.
  5. If 0 jobs remain after deduplication, runs an anthropic_web fallback
     search (max 10 results per query) and merges those in before writing.

The anthropic_web fallback path is only taken when the primary search
(Brave+Tavily + company connectors) produced nothing — e.g. both API keys
are missing or recency filtering left no results. It is not used to top up
a partial result set.
"""
import json
import logging
from pathlib import Path

from agent.state import AgentState

logger = logging.getLogger(__name__)

_JOBS_FILE = Path("query/jobs_found.jsonl")
MAX_JOBS = 50
_FALLBACK_MAX = 10


def _dedup_by_url(jobs: list[dict]) -> list[dict]:
    """Return jobs with duplicates removed; first occurrence kept."""
    seen: set[str] = set()
    out: list[dict] = []
    for job in jobs:
        url = job.get("url", "")
        if url and url not in seen:
            seen.add(url)
            out.append(job)
        elif not url:
            out.append(job)
    return out


def _write_jsonl(jobs: list[dict]) -> None:
    """Overwrite jobs_found.jsonl with the current job list."""
    _JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(j, ensure_ascii=False) for j in jobs]
    _JOBS_FILE.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def run(state: AgentState) -> AgentState:
    errors = list(state.get("errors", []))
    run_log = list(state.get("run_log", []))

    all_jobs = list(state.get("raw_jobs", []))
    unique = _dedup_by_url(all_jobs)
    capped = unique[:MAX_JOBS]

    run_log.append(
        f"aggregate_jobs: {len(all_jobs)} total → {len(unique)} unique → "
        f"{len(capped)} after cap ({MAX_JOBS} max)"
    )
    logger.info(
        "aggregate_jobs: %d total → %d unique → %d capped",
        len(all_jobs), len(unique), len(capped),
    )

    # ── Fallback: anthropic_web if primary search returned nothing ────────────
    if not capped:
        run_log.append("aggregate_jobs: 0 jobs from primary search — triggering anthropic_web fallback")
        logger.info("aggregate_jobs: triggering anthropic_web fallback")

        cfg = state["config"]
        queries = state.get("queries") or state.get("raw_queries", [])

        fallback_jobs: list[dict] = []
        if queries and cfg.get("llm"):
            try:
                from providers.llm.factory import build_llm
                from providers.search.connectors.adaptive_web import AdaptiveWebSearchProvider

                llm = build_llm(cfg["llm"], task="search")
                provider = AdaptiveWebSearchProvider(llm, cfg.get("search", {}))
                for query in queries:
                    results = provider.fallback_search(query, max_results=_FALLBACK_MAX)
                    fallback_jobs.extend(results)
                    if len(fallback_jobs) >= _FALLBACK_MAX:
                        break
                fallback_jobs = _dedup_by_url(fallback_jobs)[:_FALLBACK_MAX]
                run_log.append(f"aggregate_jobs: fallback produced {len(fallback_jobs)} jobs")
            except Exception as e:
                errors.append(f"aggregate_jobs: anthropic_web fallback failed: {e}")
                logger.error("anthropic_web fallback failed: %s", e)

        capped = fallback_jobs

    _write_jsonl(capped)
    run_log.append(f"aggregate_jobs: wrote {len(capped)} jobs to {_JOBS_FILE}")

    return {**state, "raw_jobs": capped, "errors": errors, "run_log": run_log}
