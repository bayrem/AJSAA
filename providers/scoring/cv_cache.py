"""Disk cache for compressed CV summaries.

Compression costs ~800 tokens per CV. This module persists the result keyed by
content hash so unchanged CVs are never re-compressed across runs.
Cache directory: .data/cv_cache/  (gitignored via .data/)
"""
import hashlib
import logging
from pathlib import Path

from langchain_core.messages import HumanMessage

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(".data/cv_cache")

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
    response = llm.invoke([HumanMessage(content=_COMPRESS_PROMPT.format(cv_content=cv_content))])
    return str(response.content).strip()


def get_or_compress(llm, cv: dict) -> str:
    """Return compressed CV summary — from disk cache if unchanged, LLM otherwise."""
    content_hash = hashlib.sha256(cv["content"].encode()).hexdigest()[:16]
    cache_path = _CACHE_DIR / f"{cv['name']}_{content_hash}.txt"

    if cache_path.exists():
        logger.info("CV cache hit: '%s' (hash=%s)", cv["name"], content_hash)
        return cache_path.read_text(encoding="utf-8")

    compressed = _compress(llm, cv["content"])

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(compressed, encoding="utf-8")
    logger.info("CV cache miss: '%s' compressed and cached (hash=%s)", cv["name"], content_hash)
    return compressed
