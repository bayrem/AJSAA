"""Hybrid scorer: LLM bootstrap on first run, static scoring thereafter.

The goal is to combine the qualitative judgement of LLM scoring with the
cost (and speed) of regex scoring. Per-CV lifecycle:

  1. **No profile / stale profile** — the LLM scores all jobs once, then we
     ask it to *extract* a regex profile from the highest/lowest scoring
     jobs. Profile is persisted to ``scoring_profiles/<cv>.json``.
  2. **Valid profile exists** — ``StaticScorer`` handles every job (no LLM
     calls), keyed off the same CV hash so a CV edit invalidates the profile.
  3. **Borderline jobs** — those whose static score lands inside
     ``uncertainty_band`` (e.g. ``[60, 80]``) are re-scored by the LLM to
     break ties. Jobs clearly above or below the band keep their static score.

Public API (kept stable for tests):
  - ``HybridScorer(llm, cvs, compressed_cvs, scoring_cfg)``
  - ``_extract_profile(llm, cv, scored_jobs)``
  - ``_strip_json(raw)`` — thin alias for the shared helper
"""
import json
import logging

from langchain_core.messages import HumanMessage

from providers.scoring.llm_scorer import score_jobs_batch
from providers.scoring.profile_store import content_hash, load_profile, save_profile
from providers.scoring.static_scorer import score_jobs_static
from providers.utils import strip_json_fence

logger = logging.getLogger(__name__)


# ── Prompt for profile extraction ────────────────────────────────────────────

# This prompt only runs during bootstrap — once a profile is saved we never
# call the LLM with it again for that CV (unless the CV content changes).
_EXTRACT_PROFILE_PROMPT = """\
You just scored jobs against the CV below. Now extract a keyword scoring profile that matches
terms actually present in job descriptions — not the candidate's tech stack keywords.

CV ({cv_name}) — use this to understand what kind of role we are targeting:
{cv_content}

TOP-SCORING job descriptions (these should score 80-90 with your profile):
{top_jobs}

LOW-SCORING / filtered job descriptions (these should score < 70):
{low_jobs}

Output ONLY valid JSON — no preamble, no markdown:
{{
  "cv": "{cv_name}",
  "cv_hash": "{cv_hash}",
  "positive_signals": [
    {{"pattern": "regex_pattern", "weight": 15}}
  ],
  "negative_signals": [
    {{"pattern": "junior|internship|alternance", "weight": -50}}
  ],
  "domain_bonus": {{
    "specific_term_from_top_jds": 8
  }},
  "uncertainty_band": [65, 82]
}}

KEY RULE — signals must match JOB DESCRIPTION language, not CV tech stack:
  Look at the TOP-SCORING job texts above. What phrases actually appear in those JDs
  that do NOT appear in LOW-SCORING ones? Those are your signals.
  Examples of JD language that is specific: "plateforme de données", "data platform",
  "intelligence artificielle en production", "cycle de vie", "gouvernance des données",
  "time-to-market", "parcours produit data", "roadmap data".
  Do NOT use CV backend terms (hadoop, kafka, airflow, gcp) as signals — they rarely
  appear in PM job descriptions.

CALIBRATION (sum of positive weights must be 40-55):
  - A top-scoring JD matching 4-5 signals should reach 82-90.
  - A generic "Chef de Produit IA" JD matching 1-2 signals should score 58-68.
  - Individual weights: 8-18. domain_bonus: max 2 entries ≤ 8 each.

NEGATIVE signals (3-5): junior|stagiaire|alternance, non-PM titles, pure commercial roles.
Include both English and French variants where relevant (e.g. "junior|stagiaire|alternant").
uncertainty_band: [65, 82]. Use 5-8 positive signals.\
"""


# ── Helpers ──────────────────────────────────────────────────────────────────

def _strip_json(raw: str) -> str:
    """Backwards-compatible alias for the shared helper.

    Tests import this name directly; do not delete without updating
    ``tests/test_hybrid_scorer.py``. The previous local implementation had a
    ``str.lstrip("json")`` substring bug; this alias now delegates to the
    fixed shared helper.
    """
    return strip_json_fence(raw)


def _format_jd_snippet(job: dict) -> str:
    """Format a single scored job for inclusion in the extraction prompt."""
    title = job.get("title", "")
    company = job.get("company", "")
    score = job.get("score", "?")
    desc = job.get("description", "")[:300]
    return f"[{score}] {title} @ {company}\n  {desc}"


def _extract_profile(llm, cv: dict, scored_jobs: list[dict]) -> dict:
    """Ask the LLM to distil a regex scoring profile from bootstrap results.

    Returns an empty-but-valid profile on LLM failure so the caller can still
    save a placeholder (the placeholder will be re-bootstrapped next run
    because its empty signals produce baseline-50 scores for everything).
    """
    cv_hash = content_hash(cv["content"])

    # Sort high → low so we can show the LLM "what passed" and "what didn't"
    top = sorted(scored_jobs, key=lambda j: j.get("score", 0), reverse=True)

    top_jobs = "\n\n".join(_format_jd_snippet(j) for j in top[:4])
    low_jobs = (
        "\n\n".join(_format_jd_snippet(j) for j in top[-3:])
        if len(top) > 3 else "(none below threshold)"
    )

    prompt = _EXTRACT_PROFILE_PROMPT.format(
        cv_name=cv["name"],
        cv_content=cv["content"][:600],
        top_jobs=top_jobs,
        low_jobs=low_jobs,
        cv_hash=cv_hash,
    )
    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        return json.loads(strip_json_fence(response.content))
    except Exception as e:
        logger.error("Profile extraction failed for '%s': %s — using empty profile", cv["name"], e)
        return {
            "cv": cv["name"],
            "cv_hash": cv_hash,
            "positive_signals": [],
            "negative_signals": [],
            "domain_bonus": {},
            "uncertainty_band": [60, 80],
        }


# ── Main scorer ──────────────────────────────────────────────────────────────

class HybridScorer:
    """Orchestrates bootstrap → static → optional LLM escalation."""

    def __init__(
        self,
        llm,
        cvs: list[dict],
        compressed_cvs: list[dict],
        scoring_cfg: dict,
    ) -> None:
        self.llm = llm
        # We keep both forms of each CV: the *original* content is used to
        # hash-key the profile (so a CV edit invalidates the profile), while
        # the *compressed* version goes into LLM prompts to save tokens.
        self.cvs = cvs
        self.compressed_cvs = compressed_cvs
        self.scoring_cfg = scoring_cfg
        self.profiles_dir = scoring_cfg.get("profiles_dir", "scoring_profiles")

        # The uncertainty band defines which static scores get LLM rescoring.
        band = scoring_cfg.get("uncertainty_band", [60, 80])
        self.band_lo, self.band_hi = band[0], band[1]

    def score(self, jobs: list[dict]) -> list[dict]:
        """Top-level entry point — runs bootstrap, static, and rescore phases."""
        profiles, llm_bootstrap_results = self._load_or_bootstrap_profiles(jobs)

        # When all CVs needed bootstrap, the LLM has already scored every job
        # for us — no need to run static scoring on top.
        if llm_bootstrap_results is not None:
            return llm_bootstrap_results

        all_static = score_jobs_static(jobs, profiles, self.scoring_cfg)
        certain, borderline_raw = self._partition_certain_borderline(all_static, jobs)

        if not borderline_raw:
            return sorted(certain, key=lambda j: j["score"], reverse=True)

        # Escalate borderline jobs to the LLM. We pass the *raw* jobs (not
        # the static-scored ones) so the LLM sees the original text without
        # being primed by our regex score.
        logger.info(
            "Escalating %d borderline jobs to LLM (band %d–%d)",
            len(borderline_raw), self.band_lo, self.band_hi,
        )
        llm_rescored = score_jobs_batch(
            self.llm, borderline_raw, self.compressed_cvs, self.scoring_cfg
        )
        return sorted(certain + llm_rescored, key=lambda j: j["score"], reverse=True)

    # ── Phase 1 — Profile loading / bootstrap ───────────────────────────────

    def _load_or_bootstrap_profiles(
        self,
        jobs: list[dict],
    ) -> tuple[dict[str, dict], list[dict] | None]:
        """Load existing profiles; bootstrap any that are missing or stale.

        Returns:
            ``(profiles_by_cv_name, bootstrap_results)``. When *every* CV
            required bootstrap the LLM has already scored every job, in which
            case ``bootstrap_results`` is that list and the caller should skip
            the static-scoring phase. Otherwise it is ``None``.
        """
        profiles: dict[str, dict] = {}
        needs_bootstrap: list[dict] = []

        for cv in self.cvs:
            cv_hash = content_hash(cv["content"])
            profile = load_profile(cv["name"], cv_hash, self.profiles_dir)
            if profile is None:
                needs_bootstrap.append(cv)
            else:
                profiles[cv["name"]] = profile
                logger.info("Loaded scoring profile for '%s'", cv["name"])

        if not needs_bootstrap:
            return profiles, None

        # Bootstrap: score every job with the LLM, then have the LLM emit a
        # regex profile we can reuse on subsequent runs.
        bootstrap_names = {cv["name"] for cv in needs_bootstrap}
        bootstrap_compressed = [c for c in self.compressed_cvs if c["name"] in bootstrap_names]
        logger.info("Bootstrapping profiles for: %s", sorted(bootstrap_names))

        llm_results = score_jobs_batch(
            self.llm, jobs, bootstrap_compressed, self.scoring_cfg
        )
        for cv in needs_bootstrap:
            profile = _extract_profile(self.llm, cv, llm_results)
            save_profile(profile, self.profiles_dir)
            profiles[cv["name"]] = profile

        # If *every* CV is freshly bootstrapped, the LLM has already done all
        # the scoring; signal that to the caller.
        if len(needs_bootstrap) == len(self.cvs):
            return profiles, llm_results
        return profiles, None

    # ── Phase 2 — Partition into certain vs borderline ──────────────────────

    def _partition_certain_borderline(
        self,
        all_static: list[dict],
        raw_jobs: list[dict],
    ) -> tuple[list[dict], list[dict]]:
        """Split static-scored jobs into "trust the static score" vs "ask LLM".

        Jobs whose static score lands inside ``[band_lo, band_hi]`` are
        ambiguous and worth a second opinion. Everything outside the band
        keeps its static score.
        """
        certain = [
            j for j in all_static
            if not (self.band_lo <= j["score"] <= self.band_hi)
        ]
        borderline_ids = {
            j["job_id"] for j in all_static
            if self.band_lo <= j["score"] <= self.band_hi
        }
        # We pass the *raw* version of the borderline jobs to the LLM so it
        # doesn't see our static score as a hint.
        borderline_raw = [j for j in raw_jobs if j.get("job_id") in borderline_ids]
        return certain, borderline_raw
