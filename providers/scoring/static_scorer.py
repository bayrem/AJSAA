"""Regex-based scorer that performs zero LLM calls.

Used by:
  - ``scoring.mode == "static"`` for pure offline scoring (requires a
    pre-existing profile per CV).
  - ``scoring.mode == "hybrid"`` for cheap first-pass scoring before deciding
    whether to escalate borderline jobs to the LLM.

Profile shape (one per CV)::

    {
      "positive_signals": [{"pattern": "regex", "weight": 15}, ...],
      "negative_signals": [{"pattern": "regex", "weight": -50}, ...],
      "domain_bonus":     {"regex": 8, ...},
      "uncertainty_band": [60, 80]   # used by hybrid mode
    }

Scoring starts at a baseline of 50; each matching pattern shifts the score
up or down. The result is clamped to ``[0, max_score]``.
"""
import re


class StaticScorer:
    """Score one job against one CV profile by regex pattern matching."""

    def __init__(self, profile: dict) -> None:
        # Default to empty lists/dicts so an incomplete profile doesn't crash
        # the scorer — it just produces a baseline-50 score for everything.
        self.positive = profile.get("positive_signals", [])
        self.negative = profile.get("negative_signals", [])
        self.domain_bonus = profile.get("domain_bonus", {})

    def score(self, job: dict) -> int:
        """Return a score in ``[0, 95]`` for the given job."""
        # We score against title + description as one blob so multi-word
        # patterns like "data platform" match regardless of where they appear.
        text = (job.get("title", "") + " " + job.get("description", "")).lower()
        score = 50  # baseline — every job starts here

        # Positive signals push the score up
        for sig in self.positive:
            if re.search(sig["pattern"], text, re.IGNORECASE):
                score += sig["weight"]

        # Negative signals push the score down (weights are already negative
        # in the profile so we just add them).
        for sig in self.negative:
            if re.search(sig["pattern"], text, re.IGNORECASE):
                score += sig["weight"]

        # Domain bonus is a flat additive on top — used for niche keywords
        # that should boost relevance without competing with the main signals.
        for pattern, delta in self.domain_bonus.items():
            if re.search(pattern, text, re.IGNORECASE):
                score += delta

        # Clamp to [0, 95] to match the LLM scorer's ceiling
        return max(0, min(score, 95))


def score_jobs_static(
    jobs: list[dict],
    profiles: dict[str, dict],
    scoring_cfg: dict,
) -> list[dict]:
    """Score every job against every CV profile, return the best match per job.

    Args:
        jobs: Raw job dicts.
        profiles: ``{cv_name: profile_dict}``. One profile per CV.
        scoring_cfg: Slice of config.yaml under ``scoring``. Reads
            ``min_score`` and ``max_score``.

    Returns:
        Jobs (annotated with score / best_cv / recommendation) that passed
        the ``min_score`` threshold. Jobs below the threshold are dropped.
    """
    min_score = scoring_cfg.get("min_score", 70)
    max_score_cap = scoring_cfg.get("max_score", 95)

    # Instantiate one scorer per CV up front — reuses the parsed pattern lists
    # across every job.
    scorers = {name: StaticScorer(profile) for name, profile in profiles.items()}

    results: list[dict] = []
    for job in jobs:
        # Pick the CV with the highest score for this job
        best_cv: str | None = None
        best_score = 0
        for cv_name, scorer in scorers.items():
            s = scorer.score(job)
            if s > best_score:
                best_score, best_cv = s, cv_name

        best_score = min(best_score, max_score_cap)
        if best_score < min_score:
            continue

        scored = dict(job)
        scored["score"] = best_score
        scored["best_cv"] = best_cv or ""
        scored["summary"] = ""  # static scorer has no narrative reasoning
        # 80 is the APPLY threshold across the project — keep consistent
        # with the LLM scorer's interpretation in JOB_SCORING_PROMPT.md.
        scored["recommendation"] = "APPLY" if best_score >= 80 else "CONSIDER"
        results.append(scored)

    return results
