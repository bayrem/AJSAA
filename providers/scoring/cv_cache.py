"""On-disk cache for compressed CV summaries.

Compressing a CV costs ~800 tokens per call. Running the agent daily against
an unchanged CV would burn ~24k tokens per month for the same result, so we
persist the compressed output keyed by the content hash. A CV edit changes
the hash and forces a fresh compression on the next run.

Cache files live in ``.data/cv_cache/`` (gitignored). Each file is named
``{cv_name}_{content_hash}.txt`` so multiple CVs and multiple historical
versions can coexist without collisions.
"""
import hashlib
import logging
from pathlib import Path

from langchain_core.messages import HumanMessage

logger = logging.getLogger(__name__)


# Cache directory is relative to the project root. The tests monkeypatch
# this constant to point at a tmp_path so they don't pollute the real cache.
_CACHE_DIR = Path(".data/cv_cache")


# Compression prompt — kept terse so the output is itself small enough to
# inline into every scoring prompt without exploding the context window.
_COMPRESS_PROMPT = """Extract ONLY these facts from the CV below. Be EXTREMELY concise.

CV:
{cv_content}

Output this exact format:
YOE: X years
Role: [current/most recent title]
Skills: [comma-separated top 5 technical skills]
Domain: [comma-separated top 3 domains]
Metrics: [comma-separated top 3 quantified achievements]"""


def _compress(llm, cv_content: str) -> str:
    """Send the CV through the LLM and return the compressed summary."""
    response = llm.invoke([HumanMessage(content=_COMPRESS_PROMPT.format(cv_content=cv_content))])
    return str(response.content).strip()


def get_or_compress(llm, cv: dict) -> str:
    """Return the compressed CV — from disk cache on hit, LLM on miss.

    Args:
        llm: Any LangChain-compatible chat model.
        cv: ``{"name": str, "content": str}`` (other keys are ignored).

    Returns:
        Compressed CV string suitable for inlining into scoring prompts.
    """
    # Hash the raw content (not the dict) — same content with a different
    # name should still trigger a recompression so the per-CV-name file
    # exists, but two CVs with literally identical content under different
    # names are correctly differentiated by the filename.
    content_hash = hashlib.sha256(cv["content"].encode()).hexdigest()[:16]
    cache_path = _CACHE_DIR / f"{cv['name']}_{content_hash}.txt"

    if cache_path.exists():
        logger.info("CV cache hit: '%s' (hash=%s)", cv["name"], content_hash)
        return cache_path.read_text(encoding="utf-8")

    compressed = _compress(llm, cv["content"])

    # Cache the result for next time. ``mkdir(parents=True, exist_ok=True)``
    # makes this safe even when the cache dir didn't exist before.
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(compressed, encoding="utf-8")
    logger.info("CV cache miss: '%s' compressed and cached (hash=%s)", cv["name"], content_hash)
    return compressed
