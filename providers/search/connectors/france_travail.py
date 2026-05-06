"""France Travail (formerly Pôle Emploi) connector — official French government job API.

Free registration at https://francetravail.io/data/api/offres-emploi
Requires: FRANCE_TRAVAIL_CLIENT_ID, FRANCE_TRAVAIL_CLIENT_SECRET in .env

Auth endpoint: POST https://entreprise.francetravail.fr/connexion/oauth2/access_token
Search endpoint: GET https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search
"""
import hashlib
import json
import logging
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from providers.search.connectors.base import BaseJobBoardConnector

logger = logging.getLogger(__name__)

_TOKEN_URL = (
    "https://entreprise.francetravail.fr/connexion/oauth2/access_token"
    "?realm=%2Fpartenaire"
)
_SEARCH_URL = (
    "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"
)
_SCOPE = "api_offresdemploiv2 o2dsoffre"


class FranceTravailConnector(BaseJobBoardConnector):
    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.client_id = os.environ.get("FRANCE_TRAVAIL_CLIENT_ID", "")
        self.client_secret = os.environ.get("FRANCE_TRAVAIL_CLIENT_SECRET", "")
        self._token: str | None = None
        self._token_expires: datetime = datetime.min.replace(tzinfo=timezone.utc)

    def _get_token(self) -> str:
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
        self._token_expires = now + timedelta(seconds=data.get("expires_in", 1200) - 60)
        return self._token

    def search(self, query: str, max_results: int = 10, **kwargs) -> list[dict]:
        if not self.client_id or not self.client_secret:
            logger.warning("FranceTravailConnector: credentials not set — skipping")
            return []

        try:
            token = self._get_token()
        except Exception as e:
            logger.error("FranceTravailConnector: auth failed: %s", e)
            return []

        # Use only the core terms — France Travail free-text search is strict
        core_query = query.replace(" posted last week", "").strip()
        since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")

        params = urllib.parse.urlencode({
            "motsCles": core_query,
            "commune": "75056",       # Paris INSEE code
            "distance": 10,
            "sort": 1,                # 1 = date descending
            "minCreationDate": since,
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

        jobs = []
        for item in data.get("resultats", []):
            title = item.get("intitule", "")
            company = item.get("entreprise", {}).get("nom", "")
            location = item.get("lieuTravail", {}).get("libelle", "Paris")
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
                "description": description[:1000],
                "source": "france_travail",
                "date_found": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                "status": "new",
            })

        logger.info("FranceTravailConnector: '%s' → %d results", query, len(jobs))
        return jobs
