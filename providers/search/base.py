"""Base contract for all job-search providers.

A "search provider" is anything that takes a free-text query and returns a
list of job dicts. This includes:

  - LLM-backed web searches (``AnthropicWebSearchProvider``,
    ``AdaptiveWebSearchProvider``)
  - Direct API connectors (Adzuna, France Travail, Brave, Tavily)

ATS connectors (Greenhouse, Lever, Ashby) are **not** search providers — they
take a *company slug* rather than a query, so they live under
``providers.search.connectors.ats`` with their own contract (``fetch``).
"""
from abc import ABC, abstractmethod


class BaseSearchProvider(ABC):
    """Abstract contract: ``search(query, max_results, **kwargs) -> list[dict]``.

    The default ``__init__`` accepts an optional config dict and stores it
    on ``self.cfg`` so simple connectors don't need to write their own
    initialiser. Subclasses that need additional state (e.g. an LLM handle)
    override ``__init__`` and manage ``self.cfg`` themselves.
    """

    def __init__(self, cfg: dict | None = None) -> None:
        self.cfg = cfg or {}

    @abstractmethod
    def search(self, query: str, max_results: int = 10, **kwargs) -> list[dict]:
        """Return jobs matching ``query``.

        Each returned dict must contain at least: ``title``, ``company``,
        ``location``, ``url``, ``description``. Connectors that hit an
        external API should also stamp ``job_id``, ``source``, ``date_found``
        and ``status="new"`` for storage compatibility.
        """
