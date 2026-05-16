"""LinkedIn connector — placeholder.

LinkedIn has no public job-search API. Implementation options:
  - Unofficial libraries (high ban risk; not recommended for production)
  - Headless browser scraping (fragile, ToS implications)
  - LinkedIn Recruiter API (requires a paid partnership)

Pragmatic alternative: use ``adaptive_web`` with ``target_boards: [linkedin]``,
which delegates to a search engine site-filtered to ``site:linkedin.com``.
"""
import logging

from providers.search.base import BaseSearchProvider

logger = logging.getLogger(__name__)


class LinkedInConnector(BaseSearchProvider):
    """Stub — logs a warning and returns no results until implemented."""

    def search(self, query: str, max_results: int = 10, **kwargs) -> list[dict]:
        logger.warning("LinkedInConnector is a stub — returning empty results")
        return []
