"""Web search provider using Anthropic's built-in web search tool via LangChain."""
import json
import logging
import urllib.request
from datetime import datetime, timedelta, timezone

from providers.search.base import BaseSearchProvider

logger = logging.getLogger(__name__)

# Maps board names (as used in config.yaml target_boards) to site-filter strings
# that are appended to the search query so the LLM focuses on a specific domain.
BOARD_URLS: dict[str, str] = {
    "linkedin": "site:linkedin.com",
    "wttj": "site:welcometothejungle.com",
    "indeed": "site:indeed.com",
    "apec": "site:apec.fr",
    "glassdoor": "site:glassdoor.com",
    "monster": "site:monster.fr",
    "cadremploi": "site:cadremploi.fr",
}

SEARCH_PROMPT = """Today is {today}. Search the web for job postings matching: "{query}"
{context_hint}

Only include jobs posted in the last {recency_days} days (on or after {cutoff_date}).

Return a JSON array of up to {max_results} job postings. Each item must have:
- title: job title
- company: company name
- location: city / country
- url: direct link to the posting (empty string if unknown)
- description: 1-3 sentence summary of the role
- posted_date: date posted as YYYY-MM-DD (omit field if unknown)

Return only the JSON array, no other text."""


def _validate_url(url: str, timeout: int = 5) -> bool:
    """HEAD request to verify URL is reachable. Returns True if status < 400."""
    if not url or not url.startswith("http"):
        return False
    try:
        req = urllib.request.Request(url, method="HEAD")
        req.add_header("User-Agent", "Mozilla/5.0")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status < 400
    except Exception:
        return False


def _parse_jobs(raw: str) -> list[dict]:
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    if not raw:
        raise ValueError("LLM returned empty response")

    jobs = json.loads(raw)
    if not isinstance(jobs, list):
        raise ValueError("Response is not a list")
    return jobs


class AnthropicWebSearchProvider(BaseSearchProvider):
    def __init__(self, llm, cfg: dict):
        self.llm = llm
        self.cfg = cfg

    def search(
        self,
        query: str,
        max_results: int = 10,
        context: str = "",
        board: str | None = None,
        **kwargs,
    ) -> list[dict]:
        recency_days = self.cfg.get("recency_days", 3)
        today = datetime.now(timezone.utc)
        cutoff = (today - timedelta(days=recency_days)).strftime("%Y-%m-%d")
        context_hint = f"Focus on roles relevant to: {context}" if context else ""

        # Inject site filter when a specific board is requested
        if board:
            site_filter = BOARD_URLS.get(board)
            if site_filter:
                query = f"{query} {site_filter}"
                logger.debug("Board filter applied: %s → '%s'", board, site_filter)
            else:
                logger.warning("Unknown board '%s' — no site filter applied", board)

        prompt = SEARCH_PROMPT.format(
            today=today.strftime("%Y-%m-%d"),
            query=query,
            context_hint=context_hint,
            recency_days=recency_days,
            cutoff_date=cutoff,
            max_results=max_results,
        )
        return self._execute(prompt, max_results)

    def search_with_prompt(self, prompt: str, max_results: int = 10) -> list[dict]:
        """Execute a fully pre-built prompt (used by search_companies routing)."""
        return self._execute(prompt, max_results)

    def _execute(self, prompt: str, max_results: int) -> list[dict]:
        from langchain_core.messages import HumanMessage
        validate_urls = self.cfg.get("validate_urls", True)

        try:
            response = self.llm.invoke([HumanMessage(content=prompt)])
            raw = response.content.strip()
            jobs = _parse_jobs(raw)
            results = [self._normalise(j) for j in jobs if isinstance(j, dict)]

            if validate_urls:
                valid, dropped = [], 0
                for job in results:
                    url = job.get("url", "")
                    if not url or _validate_url(url):
                        valid.append(job)
                    else:
                        dropped += 1
                        logger.debug("Dropped unreachable URL: %s", url)
                if dropped:
                    logger.info("URL validation: dropped %d unreachable job(s)", dropped)
                results = valid

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
