"""LLM-based batch job scorer.

Extracted here so both agent/nodes/analyze_jobs.py and
providers/scoring/hybrid_scorer.py can import it without a circular dependency.
"""
import json
import logging
from typing import Optional

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, ValidationError, field_validator

logger = logging.getLogger(__name__)


# ── Schema (#30) ─────────────────────────────────────────────────────────────

class ScoredJob(BaseModel):
    job_index: int
    best_cv: str
    score: int
    recommendation: str
    reasoning: str

    @field_validator("recommendation")
    @classmethod
    def valid_recommendation(cls, v: str) -> str:
        allowed = {"APPLY", "CONSIDER", "SKIP"}
        v = v.upper()
        if v not in allowed:
            raise ValueError(f"recommendation must be one of {allowed}, got '{v}'")
        return v

    @field_validator("score", mode="before")
    @classmethod
    def clamp_score(cls, v) -> int:
        return max(0, min(int(float(v)), 100))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _strip_fences(raw: str) -> str:
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0]
    elif raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return raw.strip()


def _parse_with_retry(llm, prompt: str, raw: str) -> Optional[list[ScoredJob]]:
    """Try to parse raw as a list of ScoredJob. On failure retry once with a fix prompt."""
    for attempt in range(2):
        try:
            if not raw.strip():
                raise ValueError("Empty response")
            data = json.loads(_strip_fences(raw))
            if not isinstance(data, list):
                raise ValueError("Response is not a JSON array")
            return [ScoredJob(**item) for item in data]
        except (json.JSONDecodeError, ValidationError, ValueError) as e:
            if attempt == 0:
                logger.warning("Scoring output invalid (%s) — retrying with fix prompt", e)
                fix_prompt = (
                    f"The following JSON is invalid or malformed:\n\n{raw}\n\n"
                    f"Error: {e}\n\n"
                    "Return only the corrected JSON array matching this schema:\n"
                    '[{"job_index": int, "best_cv": str, "score": int, '
                    '"recommendation": "APPLY|CONSIDER|SKIP", "reasoning": str}]'
                )
                try:
                    response = llm.invoke([HumanMessage(content=fix_prompt)])
                    raw = response.content
                except Exception as retry_err:
                    logger.error("Fix-prompt retry failed: %s", retry_err)
                    return None
            else:
                logger.error("Scoring output invalid after retry: %s", e)
                return None
    return None


# ── Public API ────────────────────────────────────────────────────────────────

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
            scored = _parse_with_retry(llm, prompt, response.content)

            if scored is None:
                logger.error("Batch %d-%d skipped — could not parse scoring output",
                             i, i + len(batch) - 1)
                continue

            batch_results = []
            for item in scored:
                if not (0 <= item.job_index < len(batch)):
                    continue
                score = min(item.score, max_score)
                if score >= min_score:
                    job = dict(batch[item.job_index])
                    job["score"] = score
                    job["best_cv"] = item.best_cv
                    job["summary"] = item.reasoning
                    job["recommendation"] = item.recommendation
                    job["strengths"] = []
                    job["gaps"] = []
                    job["red_flags"] = []
                    job["score_breakdown"] = {}
                    batch_results.append(job)

            results.extend(batch_results)
            logger.info("Batch %d-%d: %d/%d jobs passed threshold",
                        i, i + len(batch) - 1, len(batch_results), len(batch))

        except Exception as e:
            logger.error("Batch scoring failed for jobs %d-%d: %s", i, i + len(batch) - 1, e)

    return results
