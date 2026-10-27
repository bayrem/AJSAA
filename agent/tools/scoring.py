"""Scoring spoke tools — batch-score candidates and fetch postings for a second look.

``score_batch`` is the cheap, deterministic first pass (one LLM call for all
jobs, same scorer as v2). Its summary lists the *borderline* jobs (within
``scoring.borderline_margin`` of the threshold) by ``job_id`` so the scorer
subagent (M3) can decide which deserve :func:`fetch_posting` and a re-judge.
"""
import logging

from agent.run_store import CANDIDATES, DISCARDED, SCORED, RunStore

logger = logging.getLogger(__name__)

DEFAULT_BORDERLINE_MARGIN = 5
# Postings longer than this are truncated before going back to the model —
# enough for requirements and responsibilities, bounded for context size.
POSTING_CHAR_CAP = 8000
# How many top / borderline jobs the summary lists. Keeps the summary short.
SUMMARY_LIST_LIMIT = 10


def _job_line(job: dict) -> str:
    return f"[{job.get('job_id')}] {job.get('score')} — {job.get('title', '?')} @ {job.get('company', '?')}"


def score_batch(store: RunStore, cfg: dict) -> str:
    """Score every candidate against every stored CV in one LLM call.

    Writes ``scored.jsonl`` (passed) and ``discarded.jsonl`` (below threshold),
    both sorted by score, and returns counts plus top and borderline job ids.

    Raises:
        ValueError: no CVs or no candidates in the run store.
        RuntimeError: the scoring call produced no results at all (LLM or
            parse failure) — distinct from "everything scored low".
    """
    from providers.llm.factory import build_llm
    from providers.scoring.cv_cache import get_or_compress
    from providers.scoring.llm_scorer import score_jobs_batch

    cvs = store.load_cvs()
    jobs = store.read_jsonl(CANDIDATES)
    if not cvs:
        raise ValueError("No CVs in the run store — run convert_cv first")
    if not jobs:
        raise ValueError("No candidates in the run store — search first")

    scoring_cfg = cfg.get("scoring", {})
    min_score = scoring_cfg.get("min_score", 70)
    margin = scoring_cfg.get("borderline_margin", DEFAULT_BORDERLINE_MARGIN)

    # CV compression is cached on disk by content hash — paid once per CV version.
    compress_llm = build_llm(cfg["llm"], task="default")
    compressed = [{"name": cv["name"], "content": get_or_compress(compress_llm, cv)} for cv in cvs]

    passed, discarded = score_jobs_batch(build_llm(cfg["llm"], task="scoring"), jobs, compressed, scoring_cfg)
    if not passed and not discarded:
        # score_jobs_batch swallows LLM/parse failures and returns ([], []).
        # Surface that as an error so the caller doesn't read it as "all jobs weak".
        raise RuntimeError("Scoring call returned no results (LLM or parse failure — see logs)")

    passed.sort(key=lambda j: j["score"], reverse=True)
    discarded.sort(key=lambda j: j["score"], reverse=True)
    store.write_jsonl(SCORED, passed)
    store.write_jsonl(DISCARDED, discarded)

    borderline = [j for j in passed + discarded if abs(j["score"] - min_score) <= margin]
    unscored = len(jobs) - len(passed) - len(discarded)
    lines = [
        f"Scored {len(passed) + len(discarded)}/{len(jobs)} candidates"
        + (f" ({unscored} missing from model output)" if unscored else "")
        + f": {len(passed)} passed (>= {min_score}), {len(discarded)} discarded.",
        f"Borderline (within {margin} of {min_score}): {len(borderline)}.",
    ]
    if passed:
        lines.append("Top: " + "; ".join(_job_line(j) for j in passed[:SUMMARY_LIST_LIMIT]))
    if borderline:
        lines.append("Borderline: " + "; ".join(_job_line(j) for j in borderline[:SUMMARY_LIST_LIMIT]))
    return "\n".join(lines)


def fetch_posting(store: RunStore, search_cfg: dict, job_id: str) -> str:
    """Return the full posting text for one candidate (for a borderline re-check).

    Unlike the other tools this one *does* return content to the model — that
    is its purpose. Output is capped at ``POSTING_CHAR_CAP`` characters.

    Raises:
        KeyError: unknown ``job_id``.
        RuntimeError: the page could not be fetched.
    """
    from providers.search.connectors.tavily import TavilyConnector

    job = next((j for j in store.read_jsonl(CANDIDATES) if j.get("job_id") == job_id), None)
    if job is None:
        raise KeyError(f"No candidate with job_id '{job_id}'")
    url = job.get("url", "")
    content = TavilyConnector(search_cfg).extract([url]).get(url, "") if url else ""
    if not content:
        raise RuntimeError(f"Could not fetch posting for job_id '{job_id}' ({url or 'no URL'})")

    header = f"{job.get('title', '?')} @ {job.get('company', '?')} — {url}\n\n"
    return header + content[:POSTING_CHAR_CAP]
