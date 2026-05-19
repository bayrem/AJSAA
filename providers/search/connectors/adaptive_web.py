"""Adaptive web search — Brave search → Tavily extract pipeline.

Flow per query
--------------
1. Brave Search API (``freshness=pd`` — last 24 h) → list of URLs + titles.
2. Filter out aggregator / salary / listing-page URLs that don't contain
   a single job posting (Glassdoor salary pages, LinkedIn search results,
   ZipRecruiter listing pages, etc.).
3. Tavily ``/extract`` on the remaining URLs → full page text.
4. Build a job dict per extracted URL: title from Brave, description from
   Tavily content, location parsed from content, company from URL/title.

If the combined result across all queries is empty (e.g. both API keys
missing, or freshness filter left nothing), fall back to the
AnthropicWebSearchProvider (LLM web search) capped at ``_FALLBACK_MAX``
jobs — this costs LLM tokens but guarantees some output.

Monthly budget tracking for Brave and Tavily is persisted to
``query/search_usage.json`` (same file as before, new keys added).

Required env vars:
  - ``BRAVE_SEARCH_API_KEY``
  - ``TAVILY_API_KEY``
"""
from __future__ import annotations

import hashlib
import logging
import re
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from providers.search.base import BaseSearchProvider
from providers.utils import JsonCache

logger = logging.getLogger(__name__)

_USAGE_CACHE = JsonCache(Path("query/search_usage.json"))
_FALLBACK_MAX = 10

# ── URL filter ────────────────────────────────────────────────────────────────
# Patterns that identify non-posting pages (aggregator search pages, salary
# pages, review pages). Matched against the full URL (lowercased).

_BLOCKED_URL_PATTERNS: list[re.Pattern] = [re.compile(p, re.IGNORECASE) for p in [
    # Glassdoor non-postings
    r"glassdoor\.com/Salaries/",
    r"glassdoor\.com/Reviews/",
    r"glassdoor\.com/Interview/",
    r"glassdoor\.com/Overview/",
    # LinkedIn search results (not individual job views)
    r"linkedin\.com/jobs/search",
    r"linkedin\.com/jobs/$",
    # Aggregator search / listing pages
    r"indeed\.com/q-",
    r"indeed\.com/jobs\?",
    r"ziprecruiter\.com/Jobs/",
    r"monster\.com/jobs/search",
    r"simplyhired\.com/search",
    r"efinancialcareers\.com/jobs/.*in-",
    r"startup\.jobs/locations/",
    r"builtin(colorado|austin|boston|chicago|la|nyc|seattle)?\.com/jobs/",
    # Salary / comparison aggregators
    r"salary\.com",
    r"payscale\.com",
    r"levels\.fyi",
    r"glassdoor\.com/Salaries",
    # Generic search pages
    r"[?&]q=",
    r"/search[?/]",
    r"englishjobs\.fr/in/",
]]

# Patterns that strongly indicate a direct single-job posting URL.
# If a URL matches any of these, it bypasses the blocklist entirely.
_ALLOWLIST_PATTERNS: list[re.Pattern] = [re.compile(p, re.IGNORECASE) for p in [
    r"boards\.greenhouse\.io/",
    r"jobs\.lever\.co/",
    r"workday\.com/.*/(job|jobdetails)/",
    r"linkedin\.com/jobs/view/",
    r"jobs\.smartrecruiters\.com/",
    r"apply\.workable\.com/",
    r"jobs\.ashbyhq\.com/",
    r"icims\.com/jobs/",
    r"taleo\.net/careersection/",
    r"myworkdayjobs\.com/",
]]


def _is_job_posting_url(url: str) -> bool:
    """Return True if the URL looks like a direct single-job posting page."""
    for allow in _ALLOWLIST_PATTERNS:
        if allow.search(url):
            return True
    for block in _BLOCKED_URL_PATTERNS:
        if block.search(url):
            return False
    # Default: keep it — better to extract a non-job page than to miss a posting.
    return True


# ── Content parsers ───────────────────────────────────────────────────────────

def _company_from_url(url: str) -> str:
    """Best-effort company name from known ATS URL patterns."""
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower().replace("www.", "")
    path = parsed.path

    # greenhouse.io/boards/{company}/jobs/{id}
    m = re.search(r"boards\.greenhouse\.io/([^/]+)", url, re.IGNORECASE)
    if m:
        return m.group(1).replace("-", " ").title()

    # lever.co/{company}/{id}
    m = re.search(r"jobs\.lever\.co/([^/]+)", url, re.IGNORECASE)
    if m:
        return m.group(1).replace("-", " ").title()

    # Workday: {company}.wd*.myworkdayjobs.com or workday.com/{company}
    m = re.match(r"([^.]+)\.wd\d+\.myworkdayjobs\.com", host)
    if m:
        return m.group(1).replace("-", " ").title()

    # Fall back: first path segment (often the company slug on career pages)
    parts = [p for p in path.split("/") if p and p not in ("jobs", "careers", "job")]
    if parts:
        return parts[0].replace("-", " ").title()

    return host.split(".")[0].title()


def _company_from_title(title: str) -> str:
    """Parse 'Job Title at Company' or 'Job Title - Company' from a Brave title."""
    for sep in (" at ", " | ", " — ", " – ", " - "):
        if sep in title:
            parts = title.split(sep, 1)
            if len(parts) == 2:
                candidate = parts[1].strip()
                # Drop trailing noise like "| LinkedIn", "| Jobs", "| Careers"
                candidate = re.sub(r"\s*\|.*$", "", candidate).strip()
                if candidate and len(candidate) < 60:
                    return candidate
    return ""


_LOCATION_RE = re.compile(
    r"\b(Paris|Remote|Île-de-France|France|Lyon|Bordeaux|Nantes|Hybrid|On-?site)\b",
    re.IGNORECASE,
)


def _location_from_text(*texts: str) -> str:
    """Return the first location keyword found across the provided text snippets."""
    for text in texts:
        m = _LOCATION_RE.search(text or "")
        if m:
            return m.group(0).title()
    return ""


def _clean_title(title: str) -> str:
    """Strip trailing site names from Brave titles."""
    for noise in (" | LinkedIn", " - LinkedIn", " | Glassdoor", " | Indeed",
                  " | Greenhouse", " | Lever", " | Jobs", " | Careers",
                  " | Built In", " | Wellfound"):
        if title.endswith(noise):
            return title[: -len(noise)].strip()
    return title.strip()


# ── Budget tracking ───────────────────────────────────────────────────────────

def _current_month() -> str:
    return datetime.now().strftime("%Y-%m")


def _load_usage() -> dict:
    month = _current_month()
    blank: dict = {
        "tavily": {"month": month, "count": 0},
        "brave": {"month": month, "count": 0},
    }
    data = _USAGE_CACHE.load() or blank
    for key in ("tavily", "brave"):
        if data.get(key, {}).get("month") != month:
            data[key] = {"month": month, "count": 0}
    return data


# ── Main provider ─────────────────────────────────────────────────────────────

class AdaptiveWebSearchProvider(BaseSearchProvider):
    """Brave search + Tavily extract, with anthropic_web fallback on 0 results."""

    def __init__(self, llm, cfg: dict) -> None:
        super().__init__(cfg)
        self._llm = llm

        connector_cfgs = cfg.get("connectors", [])
        own_cfg = next(
            (c for c in connector_cfgs if isinstance(c, dict) and c.get("name") == "adaptive_web"),
            {},
        )
        self.limit: int = own_cfg.get("monthly_limit", 950)
        self._usage = _load_usage()
        logger.info(
            "AdaptiveWebSearch — Brave: %d/%d  Tavily: %d/%d",
            self._usage["brave"]["count"], self.limit,
            self._usage["tavily"]["count"], self.limit,
        )

    def search(self, query: str, max_results: int = 10, **kwargs) -> list[dict]:
        """Search for job postings matching *query* using Brave + Tavily extract.

        Returns a list of job dicts with real content extracted from the
        posting pages. Returns an empty list (caller must aggregate across
        queries and trigger fallback if total == 0).
        """
        from providers.search.connectors.brave import BraveConnector
        from providers.search.connectors.tavily import TavilyConnector

        brave = BraveConnector(self.cfg)
        tavily = TavilyConnector(self.cfg)

        # Step 1: Brave search → raw results
        raw_results = brave.search(query, max_results=max_results)
        self._usage["brave"]["count"] += 1
        _USAGE_CACHE.save(self._usage)

        # Step 2: filter to likely job-posting URLs
        filtered = [r for r in raw_results if _is_job_posting_url(r["url"])]
        dropped = len(raw_results) - len(filtered)
        if dropped:
            logger.info("AdaptiveWebSearch: filtered %d aggregator/non-posting URLs", dropped)

        if not filtered:
            logger.info("AdaptiveWebSearch: no posting URLs after filter for '%s'", query)
            return []

        # Step 3: Tavily extract → full content
        urls = [r["url"] for r in filtered]
        extracted = tavily.extract(urls)
        self._usage["tavily"]["count"] += 1
        _USAGE_CACHE.save(self._usage)

        # Index extracted content by URL for O(1) lookup
        content_by_url = {e["url"]: e["raw_content"] for e in extracted}

        # Step 4: build job dicts
        jobs: list[dict] = []
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        for r in filtered:
            url = r["url"]
            raw_content = content_by_url.get(url, "")
            if not raw_content:
                # Extract failed for this URL — use Brave snippet as fallback description
                raw_content = r.get("snippet", "")

            title = _clean_title(r.get("title", ""))
            company = _company_from_url(url) or _company_from_title(title)
            location = _location_from_text(title, r.get("snippet", ""), raw_content[:500])

            jobs.append({
                "job_id": hashlib.sha256(url.encode()).hexdigest()[:16],
                "title": title,
                "company": company,
                "location": location,
                "url": url,
                "description": raw_content[:2000],
                "source": "brave+tavily",
                "date_found": now,
                "status": "new",
            })

        logger.info("AdaptiveWebSearch: '%s' → %d jobs after extract", query, len(jobs))
        return jobs

    def fallback_search(self, query: str, max_results: int = _FALLBACK_MAX) -> list[dict]:
        """Run anthropic_web search as a last resort when Brave+Tavily returns nothing."""
        logger.info("AdaptiveWebSearch: 0 results from Brave+Tavily — falling back to anthropic_web")
        from providers.search.web_search import AnthropicWebSearchProvider
        return AnthropicWebSearchProvider(self._llm, self.cfg).search(query, max_results)
