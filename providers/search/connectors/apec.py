"""APEC (French executive job board) connector — placeholder.

APEC is the main French executive-focused job board (cadres). The public
search endpoint at /cms/webservices/offre/recherche returns JSON without
auth — to implement, inspect the XHR request on
https://www.apec.fr/candidat/recherche-emploi.html and replicate it here.
"""
import logging

from providers.search.base import BaseSearchProvider

logger = logging.getLogger(__name__)


class APECConnector(BaseSearchProvider):
    """Stub — logs a warning and returns no results until implemented."""

    # Documented for the eventual implementation; not used by the stub.
    BASE_URL = "https://www.apec.fr/cms/webservices/offre/recherche"

    def search(self, query: str, max_results: int = 10, **kwargs) -> list[dict]:
        logger.warning("APECConnector is a stub — returning empty results")
        return []
