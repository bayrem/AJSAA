"""Tavily Search and Extract connector.

Two capabilities:
  - ``search(query)``       — structured web search results (legacy, kept for
                              backwards compat with any tests that import it).
  - ``extract(urls)``       — fetch and parse full page content from a list of
                              URLs. Used by ``AnthropicWebSearchProvider`` to
                              validate LLM-returned job URLs and replace the
                              LLM's description with the real posting text.

Required environment variables:
  - ``TAVILY_API_KEY`` — register at https://tavily.com
"""Tavily connector — search and extract.

Provides two operations:
  - ``search(query)``  — general web search returning snippets (legacy, kept
    for any callers that haven't migrated to the Brave-search pipeline).
  - ``extract(urls)``  — fetch and clean the full text of a list of URLs via
    Tavily's /extract endpoint. Used by AdaptiveWebSearchProvider to get real
    job-posting content after Brave search returns the URLs.

Required env var: TAVILY_API_KEY
"""
import hashlib
import logging
import os
import urllib.parse
from datetime import datetime, timezone

import requests as _requests

from providers.search.base import BaseSearchProvider

logger = logging.getLogger(__name__)

_TAVILY_EXTRACT_URL = "https://api.tavily.com/extract"
_EXTRACT_BATCH_SIZE = 20  # Tavily extract accepts up to 20 URLs per call
# Tavily extract processes up to 20 URLs per call.
_EXTRACT_BATCH = 20


def _domain_hint(url: str) -> str:
    try:
        netloc = urllib.parse.urlparse(url).netloc.replace("www.", "")
        return netloc.split(".")[0].title()
    except Exception:
        return ""


class TavilyConnector(BaseSearchProvider):
    """Tavily search and extract."""

    def extract(self, urls: list[str]) -> dict[str, str]:
        """Fetch full page content for each URL via Tavily's /extract endpoint.

        Returns a dict mapping URL → raw_content for every URL that Tavily
        could successfully parse. URLs that fail (non-existent, auth-gated,
        or otherwise unscrapable) are absent from the returned dict — callers
        use this absence as a drop signal.

        Batches automatically at _EXTRACT_BATCH_SIZE. Returns an empty dict
        (and logs a warning) if TAVILY_API_KEY is not set.
        """
        api_key = os.environ.get("TAVILY_API_KEY", "")
        if not api_key:
            logger.warning("TavilyConnector.extract: TAVILY_API_KEY not set — skipping")
            return {}

        content_by_url: dict[str, str] = {}
        for i in range(0, len(urls), _EXTRACT_BATCH_SIZE):
            batch = urls[i : i + _EXTRACT_BATCH_SIZE]
            try:
                resp = _requests.post(
                    _TAVILY_EXTRACT_URL,
                    json={"urls": batch, "api_key": api_key},
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
                for result in data.get("results", []):
                    url = result.get("url", "")
                    content = result.get("raw_content", "")
                    if url and content:
                        content_by_url[url] = content
                failed = len(data.get("failed_results", []))
                logger.info(
                    "Tavily extract batch %d-%d: %d ok, %d failed",
                    i, i + len(batch), len(data.get("results", [])), failed,
                )
            except Exception as e:
                logger.error("Tavily extract batch %d-%d failed: %s", i, i + len(batch), e)

        return content_by_url

    def search(self, query: str, max_results: int = 10, **kwargs) -> list[dict]:
        """Legacy search — returns structured results as job dicts."""
    """Tavily search + extract connector."""

    # ── Search (legacy / direct use) ─────────────────────────────────────────

    def search(self, query: str, max_results: int = 10, **kwargs) -> list[dict]:
        """General web search — returns snippet-only job dicts.

        Prefer the Brave-search → extract pipeline for new code; this method
        is kept so existing callers and tests continue to work.
        """
        api_key = os.environ.get("TAVILY_API_KEY", "")
        if not api_key:
            logger.warning("TavilyConnector: TAVILY_API_KEY not set — skipping")
            return []
        try:
            from tavily import TavilyClient
            resp = TavilyClient(api_key=api_key).search(query, max_results=max_results)
        except Exception as e:
            logger.error("TavilyConnector: search failed for '%s': %s", query, e)
            return []

        jobs: list[dict] = []
        for r in resp.get("results", []):
            url = r.get("url", "")
            jobs.append({
                "job_id": hashlib.sha256(url.encode()).hexdigest()[:16],
                "title": r.get("title", ""),
                "company": _domain_hint(url),
                "location": "Paris, France",
                "url": url,
                "description": r.get("content", "")[:1000],
                "source": "tavily",
                "date_found": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                "status": "new",
            })
        logger.info("TavilyConnector.search: '%s' → %d results", query, len(jobs))
        return jobs

    # ── Extract ───────────────────────────────────────────────────────────────

    def extract(self, urls: list[str]) -> list[dict]:
        """Fetch and return cleaned full-page text for each URL.

        Calls Tavily's /extract endpoint in batches of up to 20 URLs.
        Returns ``[{"url": str, "raw_content": str}]`` for successful extracts.
        Failed URLs are logged and skipped.
        """
        api_key = os.environ.get("TAVILY_API_KEY", "")
        if not api_key:
            logger.warning("TavilyConnector: TAVILY_API_KEY not set — cannot extract")
            return []
        if not urls:
            return []

        try:
            from tavily import TavilyClient
            client = TavilyClient(api_key=api_key)
        except Exception as e:
            logger.error("TavilyConnector: failed to init client: %s", e)
            return []

        results: list[dict] = []
        for i in range(0, len(urls), _EXTRACT_BATCH):
            batch = urls[i:i + _EXTRACT_BATCH]
            try:
                resp = client.extract(urls=batch)
                for r in resp.get("results", []):
                    content = r.get("raw_content", "") or ""
                    if content.strip():
                        results.append({"url": r.get("url", ""), "raw_content": content})
                failed = resp.get("failed_results", [])
                if failed:
                    logger.warning(
                        "TavilyConnector.extract: %d URL(s) failed: %s",
                        len(failed), [f.get("url") for f in failed],
                    )
            except Exception as e:
                logger.error("TavilyConnector.extract: batch %d failed: %s", i, e)

        logger.info(
            "TavilyConnector.extract: %d/%d URLs extracted successfully",
            len(results), len(urls),
        )
        return results
