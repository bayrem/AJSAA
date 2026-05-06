"""Hybrid scorer: LLM bootstrap on first run, static scoring thereafter.

Lifecycle per CV:
  1. No profile / stale profile → LLM scores all jobs, profile extracted and saved.
  2. Valid profile exists → StaticScorer handles all jobs.
  3. Borderline jobs (score inside uncertainty_band) → re-scored by LLM.
"""
import json
import logging

from langchain_core.messages import HumanMessage

from providers.scoring.profile_store import content_hash, load_profile, save_profile
from providers.scoring.static_scorer import score_jobs_static

logger = logging.getLogger(__name__)

_EXTRACT_PROFILE_PROMPT = """\
You just scored jobs against the CV below. Extract a reusable keyword scoring profile.

CV ({cv_name}):
{cv_content}

Top and bottom scored jobs (for signal diversity):
{jobs_sample}

Output ONLY valid JSON with this exact schema — no preamble, no markdown:
{{
  "cv": "{cv_name}",
  "cv_hash": "{cv_hash}",
  "positive_signals": [
    {{"pattern": "regex_pattern", "weight": 20}}
  ],
  "negative_signals": [
    {{"pattern": "junior|internship|alternance", "weight": -50}}
  ],
  "domain_bonus": {{
    "ai|ml|llm|genai": 15
  }},
  "uncertainty_band": [60, 80]
}}

Rules:
- 5-10 positive signals, 3-5 negative signals, 2-4 domain_bonus entries
- Positive weights: 5-30. Negative weights: -50 to -5.
- Patterns must be valid Python regex (use | for alternatives).
- Base score is 50; a strong match should reach ~85.\
"""


def _strip_json(raw: str) -> str:
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0]
    elif raw.startswith("```"):
        raw = raw.lstrip("`").lstrip("json")
    return raw.strip()


def _extract_profile(llm, cv: dict, scored_jobs: list[dict]) -> dict:
    """Distil a scoring profile from LLM bootstrap results."""
    cv_hash = content_hash(cv["content"])
    top = sorted(scored_jobs, key=lambda j: j.get("score", 0), reverse=True)
    sample = (top[:5] + top[-5:]) if len(top) > 5 else top
    jobs_sample = "\n".join(
        f"- [{j.get('score', '?')}] {j.get('title', '')} @ {j.get('company', '')}: {j.get('summary', '')}"
        for j in sample
    )
    prompt = _EXTRACT_PROFILE_PROMPT.format(
        cv_name=cv["name"],
        cv_content=cv["content"][:800],
        jobs_sample=jobs_sample,
        cv_hash=cv_hash,
    )
    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        return json.loads(_strip_json(response.content))
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


class HybridScorer:
    def __init__(self, llm, cvs: list[dict], compressed_cvs: list[dict], scoring_cfg: dict):
        self.llm = llm
        self.cvs = cvs                        # original CVs (for hash + profile extraction)
        self.compressed_cvs = compressed_cvs  # compressed versions (for LLM scoring prompts)
        self.scoring_cfg = scoring_cfg
        self.profiles_dir = scoring_cfg.get("profiles_dir", "scoring_profiles")
        band = scoring_cfg.get("uncertainty_band", [60, 80])
        self.band_lo, self.band_hi = band[0], band[1]

    def score(self, jobs: list[dict]) -> list[dict]:
        # Lazy import avoids circular dependency with analyze_jobs
        from agent.nodes.analyze_jobs import score_jobs_batch

        # 1. Load profiles; collect CVs that need bootstrap
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

        # 2. Bootstrap missing profiles with one LLM pass
        if needs_bootstrap:
            bootstrap_names = {cv["name"] for cv in needs_bootstrap}
            bootstrap_compressed = [c for c in self.compressed_cvs if c["name"] in bootstrap_names]
            logger.info("Bootstrapping profiles for: %s", sorted(bootstrap_names))

            llm_results = score_jobs_batch(self.llm, jobs, bootstrap_compressed, self.scoring_cfg)
            for cv in needs_bootstrap:
                profile = _extract_profile(self.llm, cv, llm_results)
                save_profile(profile, self.profiles_dir)
                profiles[cv["name"]] = profile

            # All CVs needed bootstrap → LLM results are already authoritative
            if len(needs_bootstrap) == len(self.cvs):
                return llm_results

        # 3. Static scoring for all jobs
        all_static = score_jobs_static(jobs, profiles, self.scoring_cfg)

        # 4. Partition: certain vs borderline
        certain = [j for j in all_static if not (self.band_lo <= j["score"] <= self.band_hi)]
        borderline_ids = {
            j["job_id"] for j in all_static
            if self.band_lo <= j["score"] <= self.band_hi
        }
        borderline_raw = [j for j in jobs if j.get("job_id") in borderline_ids]

        if not borderline_raw:
            return sorted(certain, key=lambda j: j["score"], reverse=True)

        logger.info("Escalating %d borderline jobs to LLM (band %d–%d)",
                    len(borderline_raw), self.band_lo, self.band_hi)
        llm_rescored = score_jobs_batch(self.llm, borderline_raw, self.compressed_cvs, self.scoring_cfg)
        return sorted(certain + llm_rescored, key=lambda j: j["score"], reverse=True)
