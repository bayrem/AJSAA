"""France Travail (formerly Pôle Emploi) connector.

Official French government job API. Free registration at
https://francetravail.io/data/api/offres-emploi — typically takes a couple
of business days for credential approval.

Required environment variables (see ``.env.template``):
  - ``FRANCE_TRAVAIL_CLIENT_ID``
  - ``FRANCE_TRAVAIL_CLIENT_SECRET``

Uses OAuth2 client_credentials flow. The bearer token is cached on the
instance and refreshed only when expired, so a single agent run with many
queries makes at most one auth call.

Endpoints:
  - Token: POST entreprise.francetravail.fr/connexion/oauth2/access_token
  - Search: GET  api.francetravail.io/partenaire/offresdemploi/v2/offres/search
"""
import hashlib
import json
import logging
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from providers.search.base import BaseSearchProvider

logger = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────────────────────

# The ``?realm=%2Fpartenaire`` query string is required by France Travail's
# OAuth gateway — it selects the partner realm rather than the public one.
_TOKEN_URL = (
    "https://entreprise.francetravail.fr/connexion/oauth2/access_token"
    "?realm=%2Fpartenaire"
)
_SEARCH_URL = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"
# Two scopes are required for the v2 search endpoint
_SCOPE = "api_offresdemploiv2 o2dsoffre"


class FranceTravailConnector(BaseSearchProvider):
    """Connector for the France Travail public job API."""

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.client_id = os.environ.get("FRANCE_TRAVAIL_CLIENT_ID", "")
        self.client_secret = os.environ.get("FRANCE_TRAVAIL_CLIENT_SECRET", "")
        # Token cached across calls — bearer tokens are valid for ~20 minutes
        self._token: str | None = None
        self._token_expires: datetime = datetime.min.replace(tzinfo=timezone.utc)

    def _get_token(self) -> str:
        """Return a cached or freshly-fetched OAuth2 bearer token."""
        now = datetime.now(timezone.utc)
        if self._token and now < self._token_expires:
            return self._token

        body = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": _SCOPE,
        }).encode("utf-8")
        req = urllib.request.Request(
            _TOKEN_URL,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())

        self._token = data["access_token"]
        # Subtract a small safety margin so we never use a token that's
        # about to expire mid-request.
        self._token_expires = now + timedelta(seconds=data.get("expires_in", 1200) - 60)
        return self._token

    def search(self, query: str, max_results: int = 10, **kwargs) -> list[dict]:
        """Issue one France Travail search and return canonical job dicts."""
        if not self.client_id or not self.client_secret:
            logger.warning("FranceTravailConnector: credentials not set — skipping")
            return []

        try:
            token = self._get_token()
        except Exception as e:
            logger.error("FranceTravailConnector: auth failed: %s", e)
            return []

        recency_days = self.cfg.get("recency_days", 3)
        # search_jobs.py appends " last N days" to queries to nudge LLM-backed
        # backends; the FT API uses its own filter so we strip that suffix.
        core_query = query.split(" last ")[0].strip()
        since = (
            datetime.now(timezone.utc) - timedelta(days=recency_days)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

        params = urllib.parse.urlencode({
            "motsCles": core_query,
            "commune": "75056",   # INSEE code for Paris
            "distance": 10,       # km radius around Paris
            "sort": 1,            # 1 = date descending (most recent first)
            "minCreationDate": since,
            # API uses inclusive range; cap at 149 (FT's per-page max)
            "range": f"0-{min(max_results, 149) - 1}",
        })
        url = f"{_SEARCH_URL}?{params}"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
        except Exception as e:
            logger.error("FranceTravailConnector: search failed for '%s': %s", query, e)
            return []

        jobs: list[dict] = []
        for item in data.get("resultats", []):
            title = item.get("intitule", "")
            company = item.get("entreprise", {}).get("nom", "")
            location = item.get("lieuTravail", {}).get("libelle", "Paris")
            # Prefer the original posting URL; fall back to the FT detail
            # page when the employer didn't provide one.
            url_job = item.get("origineOffre", {}).get("urlOrigine") or (
                f"https://candidat.francetravail.fr/offres/recherche/detail/{item.get('id', '')}"
            )
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
                # Cap at 1000 chars to match other connectors
                "description": description[:1000],
                "source": "france_travail",
                "date_found": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                "status": "new",
            })

        logger.info("FranceTravailConnector: '%s' → %d results", query, len(jobs))
        return jobs
