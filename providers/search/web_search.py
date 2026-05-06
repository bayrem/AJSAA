"""Web search provider using Anthropic's built-in web search tool via LangChain."""
import json
import logging

from providers.search.base import BaseSearchProvider

logger = logging.getLogger(__name__)

SEARCH_PROMPT = """Search the web for job postings matching: "{query}"
{context_hint}

Return a JSON array of up to {max_results} job postings. Each item must have:
- title: job title
- company: company name
- location: city / country
- url: direct link to the posting (empty string if unknown)
- description: 1-3 sentence summary of the role

Return only the JSON array, no other text."""


class AnthropicWebSearchProvider(BaseSearchProvider):
    def __init__(self, llm, cfg: dict):
        self.llm = llm
        self.cfg = cfg

    def search(self, query: str, max_results: int = 10, context: str = "") -> list[dict]:
        from langchain_core.messages import HumanMessage

        context_hint = f"Focus on roles relevant to: {context}" if context else ""
        prompt = SEARCH_PROMPT.format(
            query=query,
            context_hint=context_hint,
            max_results=max_results,
        )

        try:
            response = self.llm.invoke([HumanMessage(content=prompt)])
            raw = response.content.strip()

            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            jobs = json.loads(raw)
            if not isinstance(jobs, list):
                raise ValueError("Response is not a list")

            return [self._normalise(j) for j in jobs if isinstance(j, dict)]

        except Exception as e:
            logger.error("Web search failed for query '%s': %s", query, e)
            return []

    def _normalise(self, job: dict) -> dict:
        return {
            "title": job.get("title", ""),
            "company": job.get("company", ""),
            "location": job.get("location", ""),
            "url": job.get("url", ""),
            "description": job.get("description", ""),
        }
