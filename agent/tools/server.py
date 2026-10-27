"""Expose the spoke tools to the Agent SDK as one in-process MCP server.

``build_tool_server(store, cfg)`` binds every tool to a run's store and
config via closures, so the model only ever supplies the small arguments
(a CV path, a list of URLs, a job id) — never job data.

Inside the SDK, tool names become ``mcp__ajsaa__<name>``; use
:func:`tool_names` when building ``allowed_tools`` lists (M2).
"""
import asyncio
import logging
from typing import Any, Callable

from agent.run_store import RunStore
from agent.tools.cv import convert_cv
from agent.tools.scoring import fetch_posting, score_batch
from agent.tools.search import ats_search, load_company_hints, validate_urls

logger = logging.getLogger(__name__)

SERVER_NAME = "ajsaa"


async def _call(fn: Callable[..., str], *args: Any) -> dict[str, Any]:
    """Run a blocking tool on a worker thread and wrap the result for MCP.

    Exceptions become ``is_error`` results instead of crashing the agent
    loop, so the orchestrator can see what failed and decide what to do.
    """
    try:
        text = await asyncio.to_thread(fn, *args)
        return {"content": [{"type": "text", "text": text}]}
    except Exception as e:
        logger.warning("tool %s failed: %s", fn.__name__, e)
        return {"content": [{"type": "text", "text": f"{fn.__name__} failed: {e}"}], "is_error": True}


def build_tools(store: RunStore, cfg: dict) -> list:
    """Return the list of ``SdkMcpTool`` objects bound to ``store`` and ``cfg``."""
    from claude_agent_sdk import tool

    search_cfg = cfg.get("search", {})

    @tool(
        "convert_cv",
        "Load a CV (.pdf or .md path) into the run as Markdown. Call once per CV before searching or scoring.",
        {"type": "object", "properties": {"cv_path": {"type": "string"}}, "required": ["cv_path"]},
    )
    async def convert_cv_tool(args: dict) -> dict:
        return await _call(convert_cv, store, args["cv_path"])

    @tool(
        "validate_urls",
        "Validate job-posting URLs (from web search or job-board tools) by fetching the real page, "
        "and save the ones that are real, reachable job postings as candidates. Pass every job URL you find here; "
        "never report jobs any other way.",
        {
            "type": "object",
            "properties": {
                "candidates": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string"},
                            "source": {"type": "string", "description": "Where the URL was found, e.g. linkedin, indeed, company_site"},
                            "found_in_snippet": {"type": "string"},
                        },
                        "required": ["url"],
                    },
                }
            },
            "required": ["candidates"],
        },
    )
    async def validate_urls_tool(args: dict) -> dict:
        return await _call(validate_urls, store, search_cfg, args["candidates"])

    @tool(
        "ats_search",
        "Fetch open jobs directly from company ATS boards (Greenhouse, Lever, Ashby) at zero token cost. "
        "Pass {company: 'greenhouse:slug' | 'lever:slug' | 'ashby:slug'}; omit company_hints to use the configured companies.",
        {
            "type": "object",
            "properties": {"company_hints": {"type": "object", "additionalProperties": {"type": "string"}}},
        },
    )
    async def ats_search_tool(args: dict) -> dict:
        hints = args.get("company_hints") or load_company_hints(cfg)
        return await _call(ats_search, store, search_cfg, hints)

    @tool(
        "score_batch",
        "Score every saved candidate against the run's CVs in one pass. Returns pass/discard counts, "
        "the top jobs and the borderline job ids.",
        {"type": "object", "properties": {}},
    )
    async def score_batch_tool(args: dict) -> dict:
        return await _call(score_batch, store, cfg)

    @tool(
        "fetch_posting",
        "Fetch the full text of one candidate posting by job_id, for a closer look at a borderline job.",
        {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]},
    )
    async def fetch_posting_tool(args: dict) -> dict:
        return await _call(fetch_posting, store, search_cfg, args["job_id"])

    return [convert_cv_tool, validate_urls_tool, ats_search_tool, score_batch_tool, fetch_posting_tool]


def build_tool_server(store: RunStore, cfg: dict) -> Any:
    """Return an ``McpSdkServerConfig`` to pass in ``ClaudeAgentOptions.mcp_servers``."""
    from claude_agent_sdk import create_sdk_mcp_server

    return create_sdk_mcp_server(name=SERVER_NAME, tools=build_tools(store, cfg))


def tool_names(tools: list) -> list[str]:
    """Fully-qualified SDK names (``mcp__ajsaa__<name>``) for ``allowed_tools``."""
    return [f"mcp__{SERVER_NAME}__{t.name}" for t in tools]
