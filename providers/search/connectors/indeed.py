"""Indeed connector — stub (RSS-based implementation planned)."""
import logging

from providers.search.connectors.base import BaseJobBoardConnector

logger = logging.getLogger(__name__)


class IndeedConnector(BaseJobBoardConnector):
    """
    Stub. Planned implementation via Indeed RSS feeds (no auth required).
    Add `feedparser` to requirements.txt, then fetch:
    https://www.indeed.com/rss?q={query}&l={location}
    """

    def search(self, query: str, max_results: int = 10, **kwargs) -> list[dict]:
        logger.warning("IndeedConnector is a stub — returning empty results")
        return []
