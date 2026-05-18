"""Semantic deduplication for job listings.

Collapses near-duplicate postings that appear across multiple sources
(e.g. the same role on Indeed, WTTJ, and a company career page) by
fuzzy-matching on (title, company, location) using stdlib difflib.

The dedup key is normalised to lowercase with stripped whitespace before
comparison so minor formatting differences don't cause false negatives.
"""
from difflib import SequenceMatcher


DEDUP_THRESHOLD = 0.85


def _normalise(value: str) -> str:
    return value.lower().strip()


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _is_duplicate(job: dict, representative: dict) -> bool:
    """Return True if job is semantically equivalent to representative.

    Compares title, company, and location independently. All three fields
    must individually meet DEDUP_THRESHOLD for the pair to be considered a
    duplicate. Using the minimum of all three (rather than an average) avoids
    false positives where two fields are identical but the third differs
    significantly — e.g. same title and company but Paris vs. London.
    """
    for field in ("title", "company", "location"):
        a = _normalise(job.get(field) or "")
        b = _normalise(representative.get(field) or "")
        if _similarity(a, b) < DEDUP_THRESHOLD:
            return False
    return True


def semantic_deduplicate(jobs: list[dict]) -> list[dict] :
    """Remove near-duplicate job listings, keeping the first occurrence.

    Two listings are considered duplicates when their normalised title,
    company, and location fields all score >= DEDUP_THRESHOLD via
    difflib.SequenceMatcher.ratio().

    Args:
        jobs: List of job dicts. Each dict is expected to have at least
              ``title``, ``company``, and ``location`` keys.

    Returns:
        A new list containing only the first occurrence of each unique role.
        Input dicts are not mutated.
    """
    kept: list[dict] = []
    for candidate in jobs:
        if not any(_is_duplicate(candidate, rep) for rep in kept):
            kept.append(candidate)
    return kept
