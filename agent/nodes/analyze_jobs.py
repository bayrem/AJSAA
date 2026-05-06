"""Score each job against all CV profiles. Keep jobs above min_score."""
import logging

from agent.state import AgentState
from providers.scoring.cv_cache import get_or_compress
from providers.scoring.llm_scorer import score_jobs_batch

logger = logging.getLogger(__name__)


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
    search_llm = build_llm(cfg["llm"], task="search")    # cheap model for extraction
    scoring_llm = build_llm(cfg["llm"], task="scoring")  # capable model for reasoning

    # Compress each CV — served from disk cache when CV is unchanged
    compressed_cvs = []
    for cv in cvs:
        try:
            compressed = get_or_compress(search_llm, cv)
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
        scorer = HybridScorer(scoring_llm, cvs, compressed_cvs, scoring_cfg)
        scored_jobs = scorer.score(raw_jobs)

    else:  # llm (default)
        scored_jobs = score_jobs_batch(scoring_llm, raw_jobs, compressed_cvs, scoring_cfg, batch_size=10)
        scored_jobs.sort(key=lambda j: j["score"], reverse=True)

    run_log.append(f"Analysis complete: {len(scored_jobs)}/{len(raw_jobs)} jobs passed threshold (≥{min_score})")
    logger.info("Analysis complete: %d/%d jobs above threshold", len(scored_jobs), len(raw_jobs))

    return {**state, "scored_jobs": scored_jobs, "errors": errors, "run_log": run_log}
