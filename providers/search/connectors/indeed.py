"""Indeed connector — placeholder.

Planned implementation via Indeed's public RSS feeds (no auth required):
  https://www.indeed.com/rss?q={query}&l={location}

Add ``feedparser`` to requirements.txt before implementing.
"""
import logging

from providers.search.base import BaseSearchProvider

logger = logging.getLogger(__name__)


class IndeedConnector(BaseSearchProvider):
    """Stub — logs a warning and returns no results until implemented."""

    def search(self, query: str, max_results: int = 10, **kwargs) -> list[dict]:
        logger.warning("IndeedConnector is a stub — returning empty results")
        return []
