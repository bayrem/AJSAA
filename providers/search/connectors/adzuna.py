"""Adzuna connector — global job aggregator with strong France coverage.

Free registration at https://developer.adzuna.com/
Requires: ADZUNA_APP_ID, ADZUNA_APP_KEY in .env
"""
import hashlib
import json
import logging
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from providers.search.connectors.base import BaseJobBoardConnector

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://api.adzuna.com/v1/api/jobs/fr/search/1"


class AdzunaConnector(BaseJobBoardConnector):
    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.app_id = os.environ.get("ADZUNA_APP_ID", "")
        self.app_key = os.environ.get("ADZUNA_APP_KEY", "")

    def search(self, query: str, max_results: int = 10, **kwargs) -> list[dict]:
        if not self.app_id or not self.app_key:
            logger.warning("AdzunaConnector: credentials not set — skipping")
            return []

        recency_days = self.cfg.get("recency_days", 3)
        core_query = query.split(" last ")[0].strip()

        params = urllib.parse.urlencode({
            "app_id": self.app_id,
            "app_key": self.app_key,
            "results_per_page": min(max_results, 50),
            "what": core_query,
            "where": "Paris",
            "sort_by": "date",
            "max_days_old": recency_days,
            "content-type": "application/json",
        })
        url = f"{_SEARCH_URL}?{params}"

        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
        except Exception as e:
            logger.error("AdzunaConnector: search failed for '%s': %s", query, e)
            return []

        jobs = []
        for item in data.get("results", []):
            title = item.get("title", "")
            company = item.get("company", {}).get("display_name", "")
            location = item.get("location", {}).get("display_name", "Paris, France")
            url_job = item.get("redirect_url", "")
            description = item.get("description", "")
            job_id = hashlib.sha256(
                f"{title}|{company}|{item.get('id', '')}".lower().encode()
            ).hexdigest()[:16]

            jobs.append({
                "job_id": job_id,
                "title": title,
                "company": company,
                "location": location,
                "url": url_job,
                "description": description[:1000],
                "source": "adzuna",
                "date_found": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                "status": "new",
            })

        logger.info("AdzunaConnector: '%s' → %d results", query, len(jobs))
        return jobs
