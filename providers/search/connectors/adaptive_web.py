"""Adaptive web search — picks the cheapest available web search backend.

Routing order (first one with budget remaining is used):
  1. Tavily (if ``TAVILY_API_KEY`` set and monthly budget remaining)
  2. Brave  (if ``BRAVE_SEARCH_API_KEY`` set and monthly budget remaining)
  3. Anthropic web search (built into the chat model — no separate budget)

Monthly request counts per backend are persisted to
``query/search_usage.json`` so budgets enforce across runs. The counter
resets automatically the first time we see a new month.
"""
import logging
import os
from datetime import datetime
from pathlib import Path

from providers.search.base import BaseSearchProvider
from providers.utils import JsonCache

logger = logging.getLogger(__name__)


# Usage counters live under query/ alongside the other operator-facing state
# (hints_cache, etc.) so users can inspect or reset them by hand.
_USAGE_CACHE = JsonCache(Path("query/search_usage.json"))


def _current_month() -> str:
    """Return the current month as YYYY-MM, used as the budget reset key."""
    return datetime.now().strftime("%Y-%m")


def _load_usage() -> dict:
    """Return the usage dict, resetting any backend whose month has rolled over."""
    month = _current_month()
    blank = {
        "tavily": {"month": month, "count": 0},
        "brave": {"month": month, "count": 0},
    }
    data = _USAGE_CACHE.load() or blank

    # Reset any backend that hasn't been touched this month
    for key in ("tavily", "brave"):
        if data.get(key, {}).get("month") != month:
            data[key] = {"month": month, "count": 0}
    return data


class AdaptiveWebSearchProvider(BaseSearchProvider):
    """Route a query to whichever web-search backend still has budget."""

    def __init__(self, llm, cfg: dict) -> None:
        # ``BaseSearchProvider.__init__`` stores ``cfg`` on ``self.cfg``;
        # we delegate to it so the contract is satisfied uniformly across
        # every search provider.
        super().__init__(cfg)
        self._llm = llm

        # The monthly request budget is configured per-connector in config.yaml,
        # not globally — different deployments may have different paid tiers.
        connector_cfgs = cfg.get("connectors", [])
        own_cfg = next(
            (c for c in connector_cfgs if isinstance(c, dict) and c.get("name") == "adaptive_web"),
            {},
        )
        self.limit: int = own_cfg.get("monthly_limit", 950)

        self._usage = _load_usage()
        logger.info(
            "Adaptive web search — Tavily: %d/%d  Brave: %d/%d",
            self._usage["tavily"]["count"], self.limit,
            self._usage["brave"]["count"], self.limit,
        )

    def search(self, query: str, max_results: int = 10, **kwargs) -> list[dict]:
        """Run ``query`` through the first backend with both API key and budget."""
        # Tavily is preferred when available — it returns structured JSON so
        # no extra LLM call is needed to parse the results.
        if self._usage["tavily"]["count"] < self.limit and os.environ.get("TAVILY_API_KEY"):
            from providers.search.connectors.tavily import TavilyConnector
            results = TavilyConnector(self.cfg).search(query, max_results, **kwargs)
            self._usage["tavily"]["count"] += 1
            _USAGE_CACHE.save(self._usage)
            return results

        # Brave is the second-line web search — keyed but cheap.
        if self._usage["brave"]["count"] < self.limit and os.environ.get("BRAVE_SEARCH_API_KEY"):
            from providers.search.connectors.brave import BraveConnector
            results = BraveConnector(self.cfg).search(query, max_results, **kwargs)
            self._usage["brave"]["count"] += 1
            _USAGE_CACHE.save(self._usage)
            return results

        # Final fallback: ask the chat model to do the web search itself.
        # No separate budget — costs roll into the model's token usage.
        logger.info("AdaptiveWebSearch: budget exhausted or keys not set — falling back to anthropic_web")
        from providers.search.web_search import AnthropicWebSearchProvider
        return AnthropicWebSearchProvider(self._llm, self.cfg).search(query, max_results, **kwargs)
