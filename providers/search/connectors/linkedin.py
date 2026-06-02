"""LinkedIn connector.

Uses the unofficial linkedin-api library (https://pypi.org/project/linkedin-api/)
as the primary search path. Falls back to stickerdaniel/linkedin-mcp-server
(browser-based automation) when the primary path fails for any reason.

Required environment variables (add via Infisical dev environment):
  - LINKEDIN_EMAIL     — LinkedIn account email
  - LINKEDIN_PASSWORD  — LinkedIn account password

MCP fallback requires a one-time setup:
  - mcp_servers/linkedin-mcp-server must be cloned and synced (see README)
  - Run: cd mcp_servers/linkedin-mcp-server && uv run -m linkedin_mcp_server --login
    This opens a browser for a one-time login; the session profile persists at
    ~/.linkedin-mcp/profile/ across runs.

NOTE: Both paths use unofficial LinkedIn access and technically violate LinkedIn's
Terms of Service. Intended for personal job search only. The connector is rate-limited
to a single concurrent request (max_concurrent: 1 in config) to reduce ban risk.
"""
import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime, timezone

from providers.search.base import BaseSearchProvider

logger = logging.getLogger(__name__)

# Resolve project root from this file's location:
# providers/search/connectors/linkedin.py → 3 levels up → project root
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
for _ in range(3):
    _PROJECT_ROOT = os.path.dirname(_PROJECT_ROOT)


class LinkedInConnector(BaseSearchProvider):
    """LinkedIn job search — unofficial API primary, MCP browser fallback."""

    def __init__(self, cfg: dict | None = None) -> None:
        super().__init__(cfg)
        self.email = os.environ.get("LINKEDIN_EMAIL", "")
        self.password = os.environ.get("LINKEDIN_PASSWORD", "")
        # Lazy-authenticated client — only created on first _search_primary() call
        self._client = None
        # MCP server command — defaults to the locally cloned server under mcp_servers/
        _mcp_dir = os.path.join(_PROJECT_ROOT, "mcp_servers", "linkedin-mcp-server")
        self.mcp_cmd: list[str] = (cfg or {}).get(
            "linkedin_mcp_cmd",
            ["uv", "run", "--directory", _mcp_dir, "-m", "linkedin_mcp_server"],
        )

    # ── Public interface ──────────────────────────────────────────────────────

    def search(self, query: str, max_results: int = 10, **kwargs) -> list[dict]:
        """Search LinkedIn jobs — tries primary API, falls back to MCP on failure."""
        if not self.email or not self.password:
            logger.warning("LinkedInConnector: LINKEDIN_EMAIL/PASSWORD not set — skipping")
            return []
        # search_jobs.py appends " last N days" for LLM-backed connectors — strip it
        core_query = query.split(" last ")[0].strip()
        try:
            return self._search_primary(core_query, max_results)
        except Exception as e:
            logger.warning(
                "LinkedInConnector: primary path failed (%s) — trying MCP fallback", e
            )
        return self._search_mcp(core_query, max_results)

    # ── Primary path: linkedin-api ────────────────────────────────────────────

    def _search_primary(self, query: str, max_results: int) -> list[dict]:
        from linkedin_api import Linkedin  # noqa: PLC0415 — lazy; keeps startup fast

        if self._client is None:
            self._client = Linkedin(self.email, self.password)

        recency_days = self.cfg.get("recency_days", 3)
        raw = self._client.search_jobs(  # type: ignore[attr-defined]
            keywords=query,
            location_name="Paris, France",
            listed_at=recency_days * 86_400,  # API expects seconds
            limit=max_results,
        )
        jobs = [j for item in raw if (j := self._map_primary_result(item)) is not None]
        logger.info("LinkedInConnector primary: '%s' → %d results", query, len(jobs))
        return jobs

    def _map_primary_result(self, item: dict) -> dict | None:
        """Convert a voyager API response item to a canonical job dict."""
        title = (item.get("title") or "").strip()
        if not title:
            return None

        # EntityUrn format: "urn:li:fsd_jobPosting:1234567"
        urn = item.get("entityUrn", "")
        job_id_li = urn.split(":")[-1] if urn else ""
        url = f"https://www.linkedin.com/jobs/view/{job_id_li}/" if job_id_li else ""

        location = item.get("formattedLocation", "")

        # Company is nested inside companyDetails — the outer key varies by API version
        company = ""
        for val in (item.get("companyDetails") or {}).values():
            if isinstance(val, dict):
                company = (
                    val.get("companyResolutionResult", {}).get("name", "")
                    or val.get("name", "")
                )
                if company:
                    break

        # Description may come as a dict with a "text" field or a plain string
        desc_field = item.get("description")
        if isinstance(desc_field, dict):
            description = (desc_field.get("text") or "")[:1000]
        elif isinstance(desc_field, str):
            description = desc_field[:1000]
        else:
            description = ""

        job_id = hashlib.sha256(
            f"{title}|{company}|{job_id_li}".lower().encode()
        ).hexdigest()[:16]

        return {
            "job_id": job_id,
            "title": title,
            "company": company,
            "location": location,
            "url": url,
            "description": description,
            "source": "linkedin",
            "date_found": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "status": "new",
        }

    # ── MCP fallback path: stickerdaniel/linkedin-mcp-server ─────────────────

    def _search_mcp(self, query: str, max_results: int) -> list[dict]:
        """Synchronous entry point — bridges to async MCP client via asyncio.run().

        asyncio.run() is safe to call from ThreadPoolExecutor worker threads
        (each thread gets its own event loop). Python 3.10+ required.
        """
        try:
            return asyncio.run(self._search_mcp_async(query, max_results))
        except Exception as e:
            logger.error("LinkedInConnector: MCP fallback failed: %s", e)
            return []

    async def _search_mcp_async(self, query: str, max_results: int) -> list[dict]:
        from mcp import ClientSession, StdioServerParameters  # noqa: PLC0415
        from mcp.client.stdio import stdio_client  # noqa: PLC0415

        server_params = StdioServerParameters(
            command=self.mcp_cmd[0],
            args=self.mcp_cmd[1:],
        )
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("search_jobs", {
                    "keywords": query,
                    "location": "Paris",
                    "date_posted": "past_week",
                    "sort_by": "date",
                })
                return self._parse_mcp_results(result, max_results, query)

    def _parse_mcp_results(self, result, max_results: int, query: str) -> list[dict]:
        """Parse TextContent from MCP call_tool result into canonical job dicts.

        The MCP server returns {job_ids: [...]} — we derive URLs from the IDs.
        title/company/description are left empty since the MCP search tool does
        not return structured fields; the downstream LLM scorer handles gaps.
        """
        try:
            raw_text = result.content[0].text if result.content else "{}"
            data = json.loads(raw_text)
        except Exception as e:
            logger.error("LinkedInConnector: could not parse MCP result: %s", e)
            return []

        job_ids = data.get("job_ids", [])[:max_results]
        jobs = []
        for jid in job_ids:
            url = f"https://www.linkedin.com/jobs/view/{jid}/"
            jobs.append({
                "job_id": hashlib.sha256(url.encode()).hexdigest()[:16],
                "title": "",
                "company": "",
                "location": "Paris",
                "url": url,
                "description": "",
                "source": "linkedin_mcp",
                "date_found": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                "status": "new",
            })

        logger.info("LinkedInConnector MCP fallback: '%s' → %d results", query, len(jobs))
        return jobs
