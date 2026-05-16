"""Welcome to the Jungle (WTTJ) connector — placeholder.

WTTJ exposes a public GraphQL endpoint at
https://api.welcometothejungle.com/graphql — no auth required for public
search queries. Implementation would build a GraphQL request and parse the
``jobs.edges`` array.
"""
import logging

from providers.search.base import BaseSearchProvider

logger = logging.getLogger(__name__)


class WTTJConnector(BaseSearchProvider):
    """Stub — logs a warning and returns no results until implemented."""

    def search(self, query: str, max_results: int = 10, **kwargs) -> list[dict]:
        logger.warning("WTTJConnector is a stub — returning empty results")
        return []
