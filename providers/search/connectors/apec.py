"""APEC (French executive job board) connector — stub."""
import logging

from providers.search.connectors.base import BaseJobBoardConnector

logger = logging.getLogger(__name__)


class APECConnector(BaseJobBoardConnector):
    """
    Stub. To implement: inspect https://www.apec.fr/candidat/recherche-emploi.html
    in browser devtools, find the XHR request, and replicate it here.
    The APEC API returns JSON — no auth required for public search.
    """

    BASE_URL = "https://www.apec.fr/cms/webservices/offre/recherche"

    def search(self, query: str, max_results: int = 10, **kwargs) -> list[dict]:
        logger.warning("APECConnector is a stub — returning empty results")
        return []
