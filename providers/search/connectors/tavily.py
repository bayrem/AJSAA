"""Tavily Search and Extract connector.

Two capabilities:
  - ``search(query)``   — structured web search results (legacy).
  - ``extract(urls)``   — fetch full page content via Tavily's /extract endpoint.
                          Used by ``url_validator`` to validate LLM-returned URLs
                          and pull real posting text.

Required environment variable: TAVILY_API_KEY
"""
import hashlib
import logging
import os
import urllib.parse
from datetime import datetime, timezone

import requests as _requests

from providers.search.base import BaseSearchProvider

logger = logging.getLogger(__name__)

_TAVILY_EXTRACT_URL = "https://api.tavily.com/extract"
_EXTRACT_BATCH_SIZE = 20


def _domain_hint(url: str) -> str:
    try:
        netloc = urllib.parse.urlparse(url).netloc.replace("www.", "")
        return netloc.split(".")[0].title()
    except Exception:
        return ""


class TavilyConnector(BaseSearchProvider):
    """Tavily search and extract."""

    def extract(self, urls: list[str]) -> dict[str, str]:
        """Fetch full page content for each URL via Tavily's /extract endpoint.

        Returns {url: raw_content} for URLs that Tavily could successfully parse.
        Absent keys mean the URL was unreachable or the content was empty —
        callers treat absence as a drop signal.
        """
        api_key = os.environ.get("TAVILY_API_KEY", "")
        if not api_key:
            logger.warning("TavilyConnector.extract: TAVILY_API_KEY not set — skipping")
            return {}

        content_by_url: dict[str, str] = {}
        for i in range(0, len(urls), _EXTRACT_BATCH_SIZE):
            batch = urls[i : i + _EXTRACT_BATCH_SIZE]
            try:
                resp = _requests.post(
                    _TAVILY_EXTRACT_URL,
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={"urls": batch},
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
                for result in data.get("results", []):
                    url = result.get("url", "")
                    content = result.get("raw_content", "")
                    if url and content:
                        content_by_url[url] = content
                failed = len(data.get("failed_results", []))
                logger.info(
                    "Tavily extract batch %d-%d: %d ok, %d failed",
                    i, i + len(batch), len(data.get("results", [])), failed,
                )
            except Exception as e:
                logger.error("Tavily extract batch %d-%d failed: %s", i, i + len(batch), e)

        return content_by_url

    def search(self, query: str, max_results: int = 10, **kwargs) -> list[dict]:
        """Legacy search — returns structured results as job dicts."""
        api_key = os.environ.get("TAVILY_API_KEY", "")
        if not api_key:
            logger.warning("TavilyConnector: TAVILY_API_KEY not set — skipping")
            return []
        try:
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
                "location": "Paris, France",
                "url": url,
                "description": r.get("content", "")[:1000],
                "source": "tavily",
                "date_found": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                "status": "new",
            })
        logger.info("TavilyConnector.search: '%s' → %d results", query, len(jobs))
        return jobs
