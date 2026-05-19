"""URL validation and content enrichment via Tavily extract.

Receives URL candidates from :mod:`providers.search.web_search` and:
  1. Calls Tavily /extract on every URL.
  2. Drops URLs that return no content (hallucinated, stale, or auth-gated).
  3. Builds a job dict for each passing URL by parsing title/company/location
     from the URL structure and location keywords from the extracted content.

Degrades gracefully if TAVILY_API_KEY is not set: returns an empty list and
logs a warning — the caller (search_jobs) handles this via fallback.
"""
import logging
import re
import urllib.parse

logger = logging.getLogger(__name__)

_MIN_CONTENT_CHARS = 200
_DESCRIPTION_CAP = 2000

_LOCATION_RE = re.compile(
    r"\b(Paris|Remote|Île-de-France|France|Lyon|Bordeaux|Nantes|Hybrid|On-?site)\b",
    re.IGNORECASE,
)


# ── Metadata extraction from URL ─────────────────────────────────────────────

def _company_from_url(url: str) -> str:
    """Best-effort company name from known ATS URL patterns."""
    # Greenhouse: job-boards.greenhouse.io/{company}/jobs/{id}
    m = re.search(r"greenhouse\.io/([^/]+)/jobs/", url, re.IGNORECASE)
    if m:
        return m.group(1).replace("-", " ").title()
    # Lever: jobs.lever.co/{company}/
    m = re.search(r"jobs\.lever\.co/([^/]+)", url, re.IGNORECASE)
    if m:
        return m.group(1).replace("-", " ").title()
    # Ashby: jobs.ashbyhq.com/{company}/
    m = re.search(r"ashbyhq\.com/([^/]+)", url, re.IGNORECASE)
    if m:
        return m.group(1).replace("-", " ").title()
    # WTTJ: welcometothejungle.com/{lang}/companies/{company}/jobs/...
    m = re.search(r"welcometothejungle\.com/[^/]+/companies/([^/]+)", url, re.IGNORECASE)
    if m:
        return m.group(1).replace("-", " ").title()
    # Workday: {company}.myworkdayjobs.com
    m = re.match(r"https?://([^.]+)\.(?:wd\d+\.)?myworkdayjobs\.com", url, re.IGNORECASE)
    if m:
        return m.group(1).replace("-", " ").title()
    # Fallback: domain name
    netloc = urllib.parse.urlparse(url).netloc.replace("www.", "")
    return netloc.split(".")[0].title()


def _title_from_url(url: str) -> str:
    """Best-effort job title from the URL path slug."""
    path = urllib.parse.urlparse(url).path
    parts = [p for p in path.split("/") if p and p not in ("jobs", "careers", "job", "fr", "en")]
    if not parts:
        return ""
    last = parts[-1]
    # Drop pure numeric IDs (Greenhouse job IDs)
    if re.match(r"^\d+$", last):
        return ""
    # Drop bare UUIDs (Lever job IDs when no title suffix)
    if re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", last, re.IGNORECASE):
        return ""
    # Lever slugs often start with a UUID prefix: "3a2b1c0d-job-title" → "job title"
    last = re.sub(r"^[0-9a-f]{8}-", "", last)
    # WTTJ format: "job-title_location" → strip location suffix
    last = last.split("_")[0]
    return last.replace("-", " ").title()


def _location_from_content(content: str) -> str:
    m = _LOCATION_RE.search(content[:1000])
    return m.group(0).title() if m else ""


def _build_job(candidate: dict, content: str) -> dict:
    """Build a job dict from a validated URL candidate and its extracted content."""
    url = candidate["url"]
    snippet = candidate.get("found_in_snippet", "")
    company = _company_from_url(url)
    title = _title_from_url(url) or snippet[:80]
    location = _location_from_content(content)
    return {
        "title": title,
        "company": company,
        "location": location,
        "url": url,
        "description": content[:_DESCRIPTION_CAP],
        "source": f"{candidate.get('source', 'other')}+tavily_extract",
    }


# ── Public API ────────────────────────────────────────────────────────────────

def validate_and_enrich(
    candidates: list[dict],
    cfg: dict,
    max_results: int = 30,
) -> list[dict]:
    """Validate URL candidates via Tavily extract and build enriched job dicts.

    Args:
        candidates:  List of ``{url, source, found_in_snippet}`` dicts from
                     :meth:`AnthropicWebSearchProvider.search_all`.
        cfg:         The search config dict (passed to TavilyConnector).
        max_results: Cap on the number of jobs to return.

    Returns:
        List of job dicts. Empty if TAVILY_API_KEY is not set.
    """
    import os
    if not os.environ.get("TAVILY_API_KEY"):
        logger.warning("url_validator: TAVILY_API_KEY not set — returning no results")
        return []

    if not candidates:
        return []

    urls = [c["url"] for c in candidates if c.get("url")]
    candidate_by_url = {c["url"]: c for c in candidates if c.get("url")}

    from providers.search.connectors.tavily import TavilyConnector
    content_by_url = TavilyConnector(cfg).extract(urls)

    jobs: list[dict] = []
    for url, content in content_by_url.items():
        if len(content) < _MIN_CONTENT_CHARS:
            logger.debug("url_validator: dropped '%s' (content too short: %d chars)", url, len(content))
            continue
        candidate = candidate_by_url.get(url, {"url": url, "source": "other", "found_in_snippet": ""})
        jobs.append(_build_job(candidate, content))

    dropped = len(urls) - len(jobs)
    logger.info(
        "url_validator: %d/%d URLs validated, %d dropped, returning %d",
        len(jobs), len(urls), dropped, min(len(jobs), max_results),
    )
    return jobs[:max_results]
