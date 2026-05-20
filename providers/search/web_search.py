"""LLM-powered web search — discovers job URLs via Claude's web search tool.

Used when ``connector: anthropic_web`` is configured.

Responsibilities (search only):
  - Build the directive prompt with positions, locations, and company hints.
  - Ask the LLM to return a URL-only JSON payload — no full job descriptions.
  - Parse and return the list of URL candidates.

Validation and content enrichment happen separately in
:mod:`providers.search.url_validator`.

Three entry points:
  - ``search_all(positions, locations, ...)`` — one comprehensive directive call
    (used by ``search_jobs``).
  - ``search(query, ...)``           — single-query search; kept for backwards
    compat and used by ``search_companies`` for focused company searches.
  - ``search_with_prompt(prompt, ...)`` — caller supplies a fully-built prompt.
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from providers.search.base import BaseSearchProvider
from providers.utils import strip_json_fence

logger = logging.getLogger(__name__)

_DIRECTIVE_PROMPT_FILE = Path(__file__).parents[2] / "query" / "SEARCH_DIRECTIVE_PROMPT.md"
_COMPANY_PROMPT_FILE = Path(__file__).parents[2] / "query" / "SEARCH_COMPANY_PROMPT.md"

_DEFAULT_DIRECTIVE = (
    "You are a job search assistant. Search for individual job postings for: {positions} "
    "in {locations}. Focus on company pages: {company_hints}. "
    "Return only jobs posted on or after {cutoff_date}. "
    'Return JSON: {{"urls": [{{"url": str, "source": str, "found_in_snippet": str}}]}}. '
    "Up to {max_results} URLs. Today is {today}. Recency: {recency_days} days."
)
_DEFAULT_COMPANY = (
    "You are a job search assistant. Search for job postings matching: \"{query}\". "
    "{context_hint} Only include jobs from the last {recency_days} days (on or after {cutoff_date}). "
    "Return a JSON array with title, company, location, url, description, posted_date. "
    "Up to {max_results} results. Today is {today}. Return only the JSON array."
)


def _load_prompt(path: Path, default: str) -> str:
    """Read a prompt template file; fall back to the inline default if missing or empty."""
    if path.exists():
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return text
    return default


BOARD_URLS: dict[str, str] = {
    "linkedin": "site:linkedin.com",
    "wttj": "site:welcometothejungle.com",
    "indeed": "site:indeed.com",
    "apec": "site:apec.fr",
    "glassdoor": "site:glassdoor.com",
    "monster": "site:monster.fr",
    "cadremploi": "site:cadremploi.fr",
}


# ── Prompts ───────────────────────────────────────────────────────────────────
# Templates live in query/SEARCH_DIRECTIVE_PROMPT.md and query/SEARCH_COMPANY_PROMPT.md.
# Edit those files to tune search behaviour without touching this module.


# ── Helpers ───────────────────────────────────────────────────────────────────

def _format_company_hints(companies: list[str], hints: dict[str, str]) -> str:
    if not companies:
        return "- (no specific companies configured)"
    lines = []
    for company in companies:
        hint = hints.get(company, "")
        if hint == "none":
            continue
        if hint.startswith("greenhouse:"):
            slug = hint.split(":", 1)[1]
            lines.append(f"- {company}: https://job-boards.greenhouse.io/{slug}")
        elif hint.startswith("lever:"):
            slug = hint.split(":", 1)[1]
            lines.append(f"- {company}: https://jobs.lever.co/{slug}")
        elif hint.startswith("ashby:"):
            slug = hint.split(":", 1)[1]
            lines.append(f"- {company}: https://jobs.ashbyhq.com/{slug}")
        elif hint.startswith("url:"):
            lines.append(f"- {company}: {hint[4:]}")
        else:
            lines.append(f"- {company}")
    return "\n".join(lines) if lines else "- (no specific companies configured)"


def _parse_url_candidates(raw: str) -> list[dict]:
    """Parse the URL-only JSON object returned by SEARCH_DIRECTIVE."""
    cleaned = strip_json_fence(raw)
    if not cleaned:
        raise ValueError("LLM returned empty response")
    data = json.loads(cleaned)
    # Accept both {"urls": [...]} and a bare list for robustness
    if isinstance(data, dict):
        urls = data.get("urls", [])
    elif isinstance(data, list):
        urls = data
    else:
        raise ValueError(f"Unexpected response type: {type(data)}")
    if not isinstance(urls, list):
        raise ValueError("urls field is not a list")
    return [u for u in urls if isinstance(u, dict) and u.get("url")]


def _parse_jobs(raw: str) -> list[dict]:
    """Parse the legacy job-dict array returned by SEARCH_PROMPT."""
    cleaned = strip_json_fence(raw)
    if not cleaned:
        raise ValueError("LLM returned empty response")
    jobs = json.loads(cleaned)
    if not isinstance(jobs, list):
        raise ValueError("Response is not a list")
    return jobs


# ── Provider ──────────────────────────────────────────────────────────────────

class AnthropicWebSearchProvider(BaseSearchProvider):
    """Discover job URLs via the chat model's built-in web search tool."""

    def __init__(self, llm, cfg: dict) -> None:
        super().__init__(cfg)
        self.llm = llm

    def search_all(
        self,
        positions: list[str],
        locations: list[str],
        companies: list[str],
        hints: dict[str, str],
        max_results: int = 50,
    ) -> list[dict]:
        """One comprehensive directive search; returns URL candidates only.

        Each candidate is ``{url, source, found_in_snippet}``. Validation and
        content enrichment are handled by :func:`providers.search.url_validator.validate_and_enrich`.
        """
        recency_days = self.cfg.get("recency_days", 3)
        today = datetime.now(timezone.utc)
        cutoff = (today - timedelta(days=recency_days)).strftime("%Y-%m-%d")

        prompt = _load_prompt(_DIRECTIVE_PROMPT_FILE, _DEFAULT_DIRECTIVE).format(
            today=today.strftime("%Y-%m-%d"),
            positions=", ".join(positions) if positions else "Product Manager",
            locations=", ".join(locations) if locations else "Paris",
            company_hints=_format_company_hints(companies, hints),
            recency_days=recency_days,
            cutoff_date=cutoff,
            max_results=max_results,
        )
        logger.info(
            "anthropic_web: directive search %d positions × %d locations, "
            "%d companies, asking for %d URLs",
            len(positions), len(locations), len(companies), max_results,
        )

        from langchain_core.messages import HumanMessage
        try:
            response = self.llm.invoke([HumanMessage(content=prompt)])
            candidates = _parse_url_candidates(response.content.strip())
            logger.info("anthropic_web: LLM returned %d URL candidates", len(candidates))
            return candidates
        except Exception as e:
            logger.error("anthropic_web directive search failed: %s", e)
            return []

    def search(
        self,
        query: str,
        max_results: int = 10,
        context: str = "",
        board: str | None = None,
        **kwargs,
    ) -> list[dict]:
        """Single-query search — used by ``search_companies``."""
        recency_days = self.cfg.get("recency_days", 3)
        today = datetime.now(timezone.utc)
        cutoff = (today - timedelta(days=recency_days)).strftime("%Y-%m-%d")
        context_hint = f"Focus on roles relevant to: {context}" if context else ""

        if board:
            site_filter = BOARD_URLS.get(board)
            if site_filter:
                query = f"{query} {site_filter}"
            else:
                logger.warning("Unknown board '%s' — no site filter applied", board)

        prompt = _load_prompt(_COMPANY_PROMPT_FILE, _DEFAULT_COMPANY).format(
            today=today.strftime("%Y-%m-%d"),
            query=query,
            context_hint=context_hint,
            recency_days=recency_days,
            cutoff_date=cutoff,
            max_results=max_results,
        )
        return self._execute_legacy(prompt, max_results)

    def search_with_prompt(self, prompt: str, max_results: int = 10) -> list[dict]:
        """Execute a fully pre-built prompt — used by ``search_companies``."""
        return self._execute_legacy(prompt, max_results)

    def _execute_legacy(self, prompt: str, max_results: int) -> list[dict]:
        """Send prompt, parse legacy job-dict array response."""
        from langchain_core.messages import HumanMessage
        try:
            response = self.llm.invoke([HumanMessage(content=prompt)])
            jobs = _parse_jobs(response.content.strip())
            results = [self._normalise(j) for j in jobs if isinstance(j, dict)]
            return results[:max_results]
        except Exception as e:
            logger.error("Web search failed for prompt (%.80s...): %s", prompt, e)
            return []

    def _normalise(self, job: dict) -> dict:
        return {
            "title": job.get("title", ""),
            "company": job.get("company", ""),
            "location": job.get("location", ""),
            "url": job.get("url", ""),
            "description": job.get("description", ""),
            "posted_date": job.get("posted_date", ""),
        }
