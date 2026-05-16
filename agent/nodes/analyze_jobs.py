"""Score every job against every CV; keep those above ``min_score``.

Three scoring modes are supported, selected via ``config.yaml -> scoring.mode``:

  - ``llm``    — Every job scored by the LLM. Highest quality, highest cost.
  - ``hybrid`` — LLM bootstraps a per-CV regex profile, then static scoring
                 handles most jobs and only borderline ones go to the LLM.
                 Best price/performance for daily runs.
  - ``static`` — Pure regex scoring against a pre-existing profile. Zero LLM
                 calls. Requires a profile to already exist (run hybrid once
                 to bootstrap one).

Two LLM handles are built per run:
  - ``search_llm``  — cheap model used for CV compression
  - ``scoring_llm`` — capable model used for actual scoring
"""
import logging

from agent.state import AgentState
from providers.scoring.cv_cache import get_or_compress
from providers.scoring.llm_scorer import score_jobs_batch

logger = logging.getLogger(__name__)


def run(state: AgentState) -> AgentState:
    """Compress CVs, score every raw job, and return ``scored_jobs`` (≥ ``min_score``)."""
    errors = list(state.get("errors", []))
    run_log = list(state.get("run_log", []))

    raw_jobs = state.get("raw_jobs", [])
    cvs = state.get("cvs", [])
    cfg = state["config"]
    scoring_cfg = cfg.get("scoring", {})
    min_score = scoring_cfg.get("min_score", 70)

    # Early-exit short-circuits — these don't count as errors.
    if not raw_jobs:
        run_log.append("No jobs to analyze")
        return {**state, "scored_jobs": [], "errors": errors, "run_log": run_log}

    if not cvs:
        errors.append("No CVs loaded — cannot score jobs")
        return {**state, "scored_jobs": [], "errors": errors, "run_log": run_log}

    # Build both LLM handles up front so configuration errors surface here
    # rather than mid-scoring.
    from providers.llm.factory import build_llm
    search_llm = build_llm(cfg["llm"], task="search")
    scoring_llm = build_llm(cfg["llm"], task="scoring")

    # Compress every CV via the disk-backed cache — repeated runs against the
    # same CV pay the LLM cost exactly once.
    compressed_cvs: list[dict] = []
    for cv in cvs:
        try:
            compressed = get_or_compress(search_llm, cv)
            compressed_cvs.append({"name": cv["name"], "content": compressed})
            run_log.append(f"Compressed CV: {cv['name']}")
        except Exception as e:
            errors.append(f"CV compression failed for '{cv['name']}': {e}")
            # Fall back to the full CV — scoring will be slower but correct.
            compressed_cvs.append(cv)

    mode = scoring_cfg.get("mode", "llm")
    run_log.append(f"Scoring mode: {mode}")

    scored_jobs: list[dict]

    if mode == "static":
        scored_jobs = _score_static(raw_jobs, cvs, scoring_cfg, errors)
    elif mode == "hybrid":
        scored_jobs = _score_hybrid(scoring_llm, raw_jobs, cvs, compressed_cvs, scoring_cfg)
    else:  # "llm" — the default
        scored_jobs = score_jobs_batch(
            scoring_llm, raw_jobs, compressed_cvs, scoring_cfg, batch_size=10
        )
        scored_jobs.sort(key=lambda j: j["score"], reverse=True)

    run_log.append(
        f"Analysis complete: {len(scored_jobs)}/{len(raw_jobs)} "
        f"jobs passed threshold (≥{min_score})"
    )
    logger.info(
        "Analysis complete: %d/%d jobs above threshold",
        len(scored_jobs), len(raw_jobs),
    )

    return {**state, "scored_jobs": scored_jobs, "errors": errors, "run_log": run_log}


def _score_static(
    raw_jobs: list[dict],
    cvs: list[dict],
    scoring_cfg: dict,
    errors: list[str],
) -> list[dict]:
    """Score with the regex scorer only. Requires a profile per CV."""
    from providers.scoring.profile_store import content_hash, load_profile
    from providers.scoring.static_scorer import score_jobs_static

    profiles_dir = scoring_cfg.get("profiles_dir", "scoring_profiles")
    profiles: dict[str, dict] = {}
    for cv in cvs:
        cv_hash = content_hash(cv["content"])
        profile = load_profile(cv["name"], cv_hash, profiles_dir)
        if profile is None:
            # Static mode can't bootstrap by itself — surface this so the
            # user knows to run hybrid mode at least once.
            errors.append(
                f"No valid scoring profile for '{cv['name']}' — "
                "run with mode: hybrid first to bootstrap"
            )
        else:
            profiles[cv["name"]] = profile

    if not profiles:
        return []

    scored = score_jobs_static(raw_jobs, profiles, scoring_cfg)
    scored.sort(key=lambda j: j["score"], reverse=True)
    return scored


def _score_hybrid(
    scoring_llm,
    raw_jobs: list[dict],
    cvs: list[dict],
    compressed_cvs: list[dict],
    scoring_cfg: dict,
) -> list[dict]:
    """Score with the hybrid scorer (regex + LLM rescoring at the band edges)."""
    from providers.scoring.hybrid_scorer import HybridScorer

    return HybridScorer(scoring_llm, cvs, compressed_cvs, scoring_cfg).score(raw_jobs)
