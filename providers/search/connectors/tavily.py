"""Tavily Search connector — structured web results.

Tavily returns already-extracted snippets so we don't pay a second LLM call
to parse a results page. Used by :class:`AdaptiveWebSearchProvider` as the
preferred web backend when ``TAVILY_API_KEY`` is set and the monthly budget
is not exhausted.

Required environment variables (see ``.env.template``):
  - ``TAVILY_API_KEY`` — register at https://tavily.com
"""
import hashlib
import logging
import os
import urllib.parse
from datetime import datetime, timezone

from providers.search.base import BaseSearchProvider

logger = logging.getLogger(__name__)


def _domain_hint(url: str) -> str:
    """Derive a rough company-name guess from a URL's domain."""
    try:
        netloc = urllib.parse.urlparse(url).netloc.replace("www.", "")
        return netloc.split(".")[0].title()
    except Exception:
        return ""


class TavilyConnector(BaseSearchProvider):
    """Issue one Tavily query and convert the results to job dicts."""

    def search(self, query: str, max_results: int = 10, **kwargs) -> list[dict]:
        api_key = os.environ.get("TAVILY_API_KEY", "")
        if not api_key:
            logger.warning("TavilyConnector: TAVILY_API_KEY not set — skipping")
            return []
        try:
            # Import lazily so the tavily package is optional — the
            # connector class can still be instantiated without it.
            from tavily import TavilyClient
            resp = TavilyClient(api_key=api_key).search(query, max_results=max_results)
        except Exception as e:
            logger.error("TavilyConnector: search failed for '%s': %s", query, e)
            return []

        jobs: list[dict] = []
        for r in resp.get("results", []):
            url = r.get("url", "")
            jobs.append({
                "job_id": hashlib.sha256(url.encode()).hexdigest()[:16],
                "title": r.get("title", ""),
                "company": _domain_hint(url),
                # Tavily doesn't surface job location; we assume Paris because
                # the only configured search queries target Paris. Downstream
                # location filtering still applies.
                "location": "Paris, France",
                "url": url,
                # Tavily snippets can be long — cap for storage size
                "description": r.get("content", "")[:1000],
                "source": "tavily",
                "date_found": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                "status": "new",
            })
        logger.info("TavilyConnector: '%s' → %d results", query, len(jobs))
        return jobs
