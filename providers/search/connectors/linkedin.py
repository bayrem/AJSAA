"""LinkedIn connector — stub."""
import logging
from providers.search.connectors.base import BaseJobBoardConnector

logger = logging.getLogger(__name__)


class LinkedInConnector(BaseJobBoardConnector):
    """
    Stub. LinkedIn does not provide a public job search API.
    Options: unofficial API libraries (high ban risk), Selenium scraping,
    or LinkedIn's official Recruiter API (requires partnership).
    """

    def search(self, query: str, max_results: int = 10, **kwargs) -> list[dict]:
        logger.warning("LinkedInConnector is a stub — returning empty results")
        return []
