"""Adaptive web search — routes queries through Tavily then Brave, falls back to anthropic_web."""
import json
import logging
import os
from datetime import datetime
from pathlib import Path

from providers.search.base import BaseSearchProvider

logger = logging.getLogger(__name__)

_USAGE_FILE = Path("query/search_usage.json")


def _current_month() -> str:
    return datetime.now().strftime("%Y-%m")


def _load_usage() -> dict:
    month = _current_month()
    blank = {"tavily": {"month": month, "count": 0}, "brave": {"month": month, "count": 0}}
    if not _USAGE_FILE.exists():
        return blank
    try:
        data = json.loads(_USAGE_FILE.read_text(encoding="utf-8"))
        for key in ("tavily", "brave"):
            if data.get(key, {}).get("month") != month:
                data[key] = {"month": month, "count": 0}
        return data
    except Exception:
        return blank


def _save_usage(usage: dict) -> None:
    try:
        _USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _USAGE_FILE.write_text(json.dumps(usage, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("AdaptiveWebSearch: could not save usage: %s", e)


class AdaptiveWebSearchProvider(BaseSearchProvider):
    def __init__(self, llm, cfg: dict) -> None:
        self.cfg = cfg
        self._llm = llm
        # Pull monthly_limit from this connector's own config entry
        connector_cfgs = cfg.get("connectors", [])
        aw_cfg = next(
            (c for c in connector_cfgs if isinstance(c, dict) and c.get("name") == "adaptive_web"),
            {},
        )
        self.limit: int = aw_cfg.get("monthly_limit", 950)
        self._usage = _load_usage()
        logger.info(
            "Adaptive web search — Tavily: %d/%d  Brave: %d/%d",
            self._usage["tavily"]["count"], self.limit,
            self._usage["brave"]["count"], self.limit,
        )

    def search(self, query: str, max_results: int = 10, **kwargs) -> list[dict]:
        if (
            self._usage["tavily"]["count"] < self.limit
            and os.environ.get("TAVILY_API_KEY")
        ):
            from providers.search.connectors.tavily import TavilyConnector
            results = TavilyConnector(self.cfg).search(query, max_results, **kwargs)
            self._usage["tavily"]["count"] += 1
            _save_usage(self._usage)
            return results

        if (
            self._usage["brave"]["count"] < self.limit
            and os.environ.get("BRAVE_SEARCH_API_KEY")
        ):
            from providers.search.connectors.brave import BraveConnector
            results = BraveConnector(self.cfg).search(query, max_results, **kwargs)
            self._usage["brave"]["count"] += 1
            _save_usage(self._usage)
            return results

        logger.info("AdaptiveWebSearch: budget exhausted or keys not set — falling back to anthropic_web")
        from providers.search.web_search import AnthropicWebSearchProvider
        return AnthropicWebSearchProvider(self._llm, self.cfg).search(query, max_results, **kwargs)
