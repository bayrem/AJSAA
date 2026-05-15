"""Lever ATS connector — unauthenticated public job board API."""
import hashlib
import json
import logging
import re
import urllib.request
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_DEFAULT_LOCATION_KEYWORDS = ["paris", "france", "remote", "télétravail", "hybrid", "île-de-france"]
_BASE_URL = "https://api.lever.co/v0/postings/{slug}?mode=json"
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-]*$")


class LeverConnector:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg

    def fetch(self, slug: str, location_keywords: list[str] | None = None) -> list[dict]:
        """Fetch all open jobs for company *slug* from Lever."""
        if not _SLUG_RE.match(slug):
            logger.error("LeverConnector: invalid slug '%s' — skipping", slug)
            return []
        url = _BASE_URL.format(slug=slug)
        keywords = location_keywords if location_keywords is not None else _DEFAULT_LOCATION_KEYWORDS
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
        except Exception as e:
            logger.error("LeverConnector: request failed for '%s': %s", slug, e)
            return []

        jobs = []
        for item in data:
            location = item.get("categories", {}).get("location", "")
            if keywords and not any(kw in location.lower() for kw in keywords):
                continue
            job_url = item.get("hostedUrl", "")
            jobs.append({
                "job_id": hashlib.sha256(job_url.encode()).hexdigest()[:16],
                "title": item.get("text", ""),
                "company": slug.title(),
                "location": location,
                "url": job_url,
                "description": item.get("descriptionPlain", "")[:1000],
                "source": "lever",
                "date_found": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                "status": "new",
            })

        logger.info("LeverConnector: '%s' → %d results", slug, len(jobs))
        return jobs
