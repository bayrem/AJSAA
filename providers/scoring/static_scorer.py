"""Keyword-based scorer that makes zero LLM calls.

Requires a scoring profile (bootstrapped by hybrid mode or hand-crafted).
Score baseline is 50; signals push it up or down; result is clamped to [0, 95].
"""
import re


class StaticScorer:
    def __init__(self, profile: dict):
        self.positive = profile.get("positive_signals", [])
        self.negative = profile.get("negative_signals", [])
        self.domain_bonus = profile.get("domain_bonus", {})

    def score(self, job: dict) -> int:
        text = (job.get("title", "") + " " + job.get("description", "")).lower()
        score = 50
        for sig in self.positive:
            if re.search(sig["pattern"], text, re.IGNORECASE):
                score += sig["weight"]
        for sig in self.negative:
            if re.search(sig["pattern"], text, re.IGNORECASE):
                score += sig["weight"]  # weight is already negative
        for pattern, delta in self.domain_bonus.items():
            if re.search(pattern, text, re.IGNORECASE):
                score += delta
        return max(0, min(score, 95))


def score_jobs_static(jobs: list[dict], profiles: dict[str, dict], scoring_cfg: dict) -> list[dict]:
    """Score jobs against all profiles; pick the best CV per job."""
    min_score = scoring_cfg.get("min_score", 70)
    max_score_cap = scoring_cfg.get("max_score", 95)
    scorers = {name: StaticScorer(profile) for name, profile in profiles.items()}

    results = []
    for job in jobs:
        best_cv, best_score = None, 0
        for cv_name, scorer in scorers.items():
            s = scorer.score(job)
            if s > best_score:
                best_score, best_cv = s, cv_name

        best_score = min(best_score, max_score_cap)
        if best_score >= min_score:
            scored = dict(job)
            scored["score"] = best_score
            scored["best_cv"] = best_cv or ""
            scored["summary"] = ""
            scored["recommendation"] = "APPLY" if best_score >= 80 else "CONSIDER"
            scored["strengths"] = []
            scored["gaps"] = []
            scored["red_flags"] = []
            scored["score_breakdown"] = {}
            results.append(scored)

    return results
