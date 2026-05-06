"""LLM-based batch job scorer.

Extracted here so both agent/nodes/analyze_jobs.py and
providers/scoring/hybrid_scorer.py can import it without a circular dependency.
"""
import json
import logging

from langchain_core.messages import HumanMessage

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
