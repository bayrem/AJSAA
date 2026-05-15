"""Tavily Search connector — structured web results, no LLM call at search time."""
import hashlib
import logging
import os
import urllib.parse
from datetime import datetime, timezone

from providers.search.connectors.base import BaseJobBoardConnector

logger = logging.getLogger(__name__)


def _domain_hint(url: str) -> str:
    try:
        netloc = urllib.parse.urlparse(url).netloc.replace("www.", "")
        return netloc.split(".")[0].title()
    except Exception:
        return ""


class TavilyConnector(BaseJobBoardConnector):
    def search(self, query: str, max_results: int = 10, **kwargs) -> list[dict]:
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

        jobs = []
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
        logger.info("TavilyConnector: '%s' → %d results", query, len(jobs))
        return jobs
