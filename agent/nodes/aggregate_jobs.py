"""Aggregate, deduplicate, and checkpoint all found jobs.

Sits between the two search nodes (search_jobs, search_companies) and
analyze_jobs. Merges raw_jobs from both, deduplicates by URL, caps at
MAX_JOBS, and writes the result to ``query/jobs_found.jsonl`` as a
checkpoint that can be inspected independently of the run report.
"""
import json
import logging
from pathlib import Path

from agent.state import AgentState

logger = logging.getLogger(__name__)

_JOBS_FILE = Path("query/jobs_found.jsonl")
MAX_JOBS = 50


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

    _write_jsonl(capped)
    run_log.append(f"aggregate_jobs: wrote {len(capped)} jobs to {_JOBS_FILE}")

    return {**state, "raw_jobs": capped, "errors": errors, "run_log": run_log}
