"""Brave Search connector — web results via Brave Search API."""
import hashlib
import logging
import os
import urllib.parse
from datetime import datetime, timezone

import requests

from providers.search.connectors.base import BaseJobBoardConnector

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"


def _domain_hint(url: str) -> str:
    try:
        netloc = urllib.parse.urlparse(url).netloc.replace("www.", "")
        return netloc.split(".")[0].title()
    except Exception:
        return ""


class BraveConnector(BaseJobBoardConnector):
    def search(self, query: str, max_results: int = 10, **kwargs) -> list[dict]:
        api_key = os.environ.get("BRAVE_SEARCH_API_KEY", "")
        if not api_key:
            logger.warning("BraveConnector: BRAVE_SEARCH_API_KEY not set — skipping")
            return []
        try:
            resp = requests.get(
                _SEARCH_URL,
                params={"q": query, "count": str(min(max_results, 20))},
                headers={"Accept": "application/json", "X-Subscription-Token": api_key},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error("BraveConnector: search failed for '%s': %s", query, e)
            return []

        jobs = []
        for r in data.get("web", {}).get("results", []):
            url = r.get("url", "")
            jobs.append({
                "job_id": hashlib.sha256(url.encode()).hexdigest()[:16],
                "title": r.get("title", ""),
                "company": _domain_hint(url),
                "location": "Paris, France",
                "url": url,
                "description": r.get("description", "")[:1000],
                "source": "brave",
                "date_found": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                "status": "new",
            })
        logger.info("BraveConnector: '%s' → %d results", query, len(jobs))
        return jobs
