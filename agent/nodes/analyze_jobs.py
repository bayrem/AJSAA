"""Score each job against all CV profiles. Keep jobs above min_score."""
import json
import logging

from langchain_core.messages import HumanMessage

from agent.state import AgentState
from providers.scoring.cv_cache import get_or_compress

logger = logging.getLogger(__name__)


def _strip_fences(raw: str) -> str:
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0]
    elif raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return raw.strip()


def score_jobs_batch(llm, jobs: list[dict], compressed_cvs: list[dict],
                     scoring_cfg: dict, batch_size: int = 10) -> list[dict]:
    """Score jobs in batches of batch_size. Returns only jobs with score >= min_score."""
    min_score = scoring_cfg.get("min_score", 70)
    max_score = scoring_cfg.get("max_score", 95)
    results = []

    cvs_text = "\n\n".join(
        f"{cv['name']}:\n{cv['content']}" for cv in compressed_cvs
    )

    for i in range(0, len(jobs), batch_size):
        batch = jobs[i:i + batch_size]

        jobs_text = "\n\n".join(
            f"JOB {j}: {job.get('title', '')} at {job.get('company', '')}\n"
            f"Location: {job.get('location', '')}\n"
            f"Desc: {job.get('description', '')[:300]}"
            for j, job in enumerate(batch)
        )

        prompt = f"""Score these {len(batch)} jobs against the CV profiles below.

CVs:
{cvs_text}

Jobs:
{jobs_text}

Rules:
- Score 0-{max_score}. Only include jobs with score >= {min_score}.
- Base score strictly on CV facts — no assumptions.
- Return JSON array only, no preamble.

Output format:
[
  {{"job_index": 0, "best_cv": "cv_name", "score": 82, "recommendation": "APPLY", "reasoning": "one sentence"}},
  {{"job_index": 2, "best_cv": "cv_name", "score": 75, "recommendation": "CONSIDER", "reasoning": "one sentence"}}
]

Omit jobs scoring below {min_score}."""

        try:
            response = llm.invoke([HumanMessage(content=prompt)])
            batch_scores = json.loads(_strip_fences(response.content))

            for item in batch_scores:
                idx = item.get("job_index")
                if idx is None or not (0 <= idx < len(batch)):
                    continue
                score = min(int(float(item.get("score", 0))), max_score)
                if score >= min_score:
                    job = dict(batch[idx])
                    job["score"] = score
                    job["best_cv"] = item.get("best_cv", "")
                    job["summary"] = item.get("reasoning", "")
                    job["recommendation"] = item.get("recommendation", "")
                    job["strengths"] = []
                    job["gaps"] = []
                    job["red_flags"] = []
                    job["score_breakdown"] = {}
                    results.append(job)

            logger.info("Batch %d-%d: %d/%d jobs passed threshold",
                        i, i + len(batch) - 1, len(batch_scores), len(batch))

        except Exception as e:
            logger.error("Batch scoring failed for jobs %d-%d: %s", i, i + len(batch) - 1, e)

    return results


def run(state: AgentState) -> AgentState:
    errors = list(state.get("errors", []))
    run_log = list(state.get("run_log", []))

    raw_jobs = state.get("raw_jobs", [])
    cvs = state.get("cvs", [])
    cfg = state["config"]
    scoring_cfg = cfg.get("scoring", {})
    min_score = scoring_cfg.get("min_score", 70)

    if not raw_jobs:
        run_log.append("No jobs to analyze")
        return {**state, "scored_jobs": [], "errors": errors, "run_log": run_log}

    if not cvs:
        errors.append("No CVs loaded — cannot score jobs")
        return {**state, "scored_jobs": [], "errors": errors, "run_log": run_log}

    from providers.llm.factory import build_llm
    llm = build_llm(cfg["llm"])

    # Compress each CV — served from disk cache when CV is unchanged
    compressed_cvs = []
    for cv in cvs:
        try:
            compressed = get_or_compress(llm, cv)
            compressed_cvs.append({"name": cv["name"], "content": compressed})
            run_log.append(f"Compressed CV: {cv['name']}")
        except Exception as e:
            errors.append(f"CV compression failed for '{cv['name']}': {e}")
            compressed_cvs.append(cv)  # fall back to full CV

    mode = scoring_cfg.get("mode", "llm")
    run_log.append(f"Scoring mode: {mode}")

    if mode == "static":
        from providers.scoring.profile_store import content_hash, load_profile
        from providers.scoring.static_scorer import score_jobs_static
        profiles_dir = scoring_cfg.get("profiles_dir", "scoring_profiles")
        profiles = {}
        for cv in cvs:
            cv_hash = content_hash(cv["content"])
            profile = load_profile(cv["name"], cv_hash, profiles_dir)
            if profile is None:
                errors.append(
                    f"No valid scoring profile for '{cv['name']}' — "
                    "run with mode: hybrid first to bootstrap"
                )
            else:
                profiles[cv["name"]] = profile
        if not profiles:
            return {**state, "scored_jobs": [], "errors": errors, "run_log": run_log}
        scored_jobs = score_jobs_static(raw_jobs, profiles, scoring_cfg)
        scored_jobs.sort(key=lambda j: j["score"], reverse=True)

    elif mode == "hybrid":
        from providers.scoring.hybrid_scorer import HybridScorer
        scorer = HybridScorer(llm, cvs, compressed_cvs, scoring_cfg)
        scored_jobs = scorer.score(raw_jobs)

    else:  # llm (default)
        scored_jobs = score_jobs_batch(llm, raw_jobs, compressed_cvs, scoring_cfg, batch_size=10)
        scored_jobs.sort(key=lambda j: j["score"], reverse=True)

    run_log.append(f"Analysis complete: {len(scored_jobs)}/{len(raw_jobs)} jobs passed threshold (≥{min_score})")
    logger.info("Analysis complete: %d/%d jobs above threshold", len(scored_jobs), len(raw_jobs))

    return {**state, "scored_jobs": scored_jobs, "errors": errors, "run_log": run_log}
