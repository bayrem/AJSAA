"""Brave Search connector — returns search-result URLs for downstream extraction.

Used by AdaptiveWebSearchProvider as the search leg of the
Brave-search → Tavily-extract pipeline. Returns raw search results
(url, title, snippet) so the caller can batch-extract content via Tavily.

Required env var: BRAVE_SEARCH_API_KEY
"""
import logging
import os

import requests

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"


class BraveConnector:
    """Issue one Brave query and return raw search results."""

    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg

    def search(self, query: str, max_results: int = 10) -> list[dict]:
        """Return up to *max_results* search results as ``{url, title, snippet}`` dicts.

        ``freshness=pd`` (past day) keeps results to the last 24 hours.
        Returns an empty list on error or missing credentials.
        """
        api_key = os.environ.get("BRAVE_SEARCH_API_KEY", "")
        if not api_key:
            logger.warning("BraveConnector: BRAVE_SEARCH_API_KEY not set — skipping")
            return []
        try:
            resp = requests.get(
                _SEARCH_URL,
                params={
                    "q": query,
                    "count": str(min(max_results, 20)),
                    "freshness": "pd",          # past day — last 24h only
                    "result_filter": "web",
                },
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": api_key,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error("BraveConnector: search failed for '%s': %s", query, e)
            return []

        results = []
        for r in data.get("web", {}).get("results", []):
            url = r.get("url", "")
            if not url:
                continue
            results.append({
                "url": url,
                "title": r.get("title", ""),
                "snippet": r.get("description", ""),
            })

        logger.info("BraveConnector: '%s' → %d results", query, len(results))
        return results
