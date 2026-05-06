"""Welcome to the Jungle (WTTJ) connector — stub."""
import logging
from providers.search.connectors.base import BaseJobBoardConnector

logger = logging.getLogger(__name__)


class WTTJConnector(BaseJobBoardConnector):
    """
    Stub. WTTJ has a public GraphQL API at https://api.welcometothejungle.com/graphql.
    No auth required for public job search queries.
    """

    def search(self, query: str, max_results: int = 10, **kwargs) -> list[dict]:
        logger.warning("WTTJConnector is a stub — returning empty results")
        return []
