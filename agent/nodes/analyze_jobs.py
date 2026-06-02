"""Score every job in ``query/jobs_found.jsonl`` against every CV; keep those above ``min_score``.

Input:  ``query/jobs_found.jsonl`` — written by aggregate_jobs, one job per line.
Output: ``query/jobs_scored.jsonl`` — same lines with ``score``, ``best_cv``,
        ``recommendation``, and ``reasoning`` appended.

Scoring is a single LLM call for all jobs (no batching, no hybrid/static modes).
The compressed CV cache is used so CV compression is paid exactly once per CV.
"""
import json
import logging
from pathlib import Path

from agent.state import AgentState
from providers.scoring.cv_cache import get_or_compress
from providers.scoring.llm_scorer import score_jobs_batch

logger = logging.getLogger(__name__)

_JOBS_FILE = Path("query/jobs_found.jsonl")
_SCORED_FILE = Path("query/jobs_scored.jsonl")
_DISCARDED_FILE = Path("query/jobs_discarded.jsonl")


def _read_jobs_jsonl() -> list[dict]:
    if not _JOBS_FILE.exists():
        return []
    with _JOBS_FILE.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _write_jsonl(path: Path, jobs: list[dict]) -> None:
    lines = [json.dumps(j, ensure_ascii=False) for j in jobs]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def run(state: AgentState) -> AgentState:
    """Read jobs from JSONL checkpoint, score in one LLM call, write scored JSONL."""
    errors = list(state.get("errors", []))
    run_log = list(state.get("run_log", []))

    cvs = state.get("cvs", [])
    cfg = state["config"]
    scoring_cfg = cfg.get("scoring", {})
    min_score = scoring_cfg.get("min_score", 70)

    # Read from the JSONL checkpoint written by aggregate_jobs.
    # Fall back to state["raw_jobs"] for test runs that skip aggregate_jobs.
    raw_jobs = _read_jobs_jsonl()
    if not raw_jobs:
        raw_jobs = state.get("raw_jobs", [])
        if raw_jobs:
            run_log.append("analyze_jobs: JSONL checkpoint not found — using state raw_jobs")

    if not raw_jobs:
        run_log.append("No jobs to analyze")
        return {**state, "scored_jobs": [], "discarded_jobs": [], "errors": errors, "run_log": run_log}

    if not cvs:
        errors.append("No CVs loaded — cannot score jobs")
        return {**state, "scored_jobs": [], "discarded_jobs": [], "errors": errors, "run_log": run_log}

    from providers.llm.factory import build_llm
    search_llm = build_llm(cfg["llm"], task="search")
    scoring_llm = build_llm(cfg["llm"], task="scoring")

    compressed_cvs: list[dict] = []
    for cv in cvs:
        try:
            compressed = get_or_compress(search_llm, cv)
            compressed_cvs.append({"name": cv["name"], "content": compressed})
            run_log.append(f"Compressed CV: {cv['name']}")
        except Exception as e:
            errors.append(f"CV compression failed for '{cv['name']}': {e}")
            compressed_cvs.append(cv)

    scored_jobs, discarded_jobs = score_jobs_batch(scoring_llm, raw_jobs, compressed_cvs, scoring_cfg)
    scored_jobs.sort(key=lambda j: j["score"], reverse=True)
    discarded_jobs.sort(key=lambda j: j["score"], reverse=True)

    _write_jsonl(_SCORED_FILE, scored_jobs)
    _write_jsonl(_DISCARDED_FILE, discarded_jobs)
    run_log.append(
        f"analyze_jobs: wrote {len(scored_jobs)} scored + {len(discarded_jobs)} discarded"
    )

    run_log.append(
        f"Analysis complete: {len(scored_jobs)}/{len(raw_jobs)} "
        f"jobs passed threshold (≥{min_score}), {len(discarded_jobs)} discarded"
    )
    logger.info(
        "Analysis complete: %d/%d jobs above threshold, %d discarded",
        len(scored_jobs), len(raw_jobs), len(discarded_jobs),
    )

    return {
        **state,
        "scored_jobs": scored_jobs,
        "discarded_jobs": discarded_jobs,
        "errors": errors,
        "run_log": run_log,
    }
