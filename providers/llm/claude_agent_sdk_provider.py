"""LLM provider backed by the Claude Agent SDK (``claude-agent-sdk``).

Successor to ``claude_code_provider`` (which shells out to ``claude -p``).
The Agent SDK runs the same Claude Code engine, but as a Python library: we
get typed result objects, real system prompts, an explicit tool allow-list,
and per-call cost/usage without parsing CLI stdout.

Billing is an explicit config switch (``llm.billing``):

  - ``subscription`` — calls bill your Claude Pro/Max plan via the logged-in
    ``claude`` session. ``ANTHROPIC_API_KEY`` is blanked for the SDK
    subprocess so a key injected by Infisical can never silently switch the
    run to API billing (the SDK subprocess inherits the parent environment).
  - ``api`` — calls bill the Anthropic API. The key is read from the
    non-default secret name ``AJSAA_ANTHROPIC_API_KEY`` and passed to the SDK
    only in this mode.

Isolation: ``setting_sources=[]`` stops the SDK from loading your
``~/.claude`` settings, CLAUDE.md and hooks; ``skills=[]`` hides skills; and
``strict_mcp_config=True`` loads only the MCP servers passed in explicitly.
The last one matters most: without it a subscription login pulls in every
claude.ai connector (Gmail, Drive, Slack, …) — measured at ~51k extra cached
tokens per Sonnet call, a ~10× cost increase per call.
Tools are allow-listed: search tasks get ``WebSearch``/``WebFetch`` only,
every other task gets no tools at all (the legacy CLI path granted every
tool, including Bash, via ``--dangerously-skip-permissions``).

The ``ClaudeAgentSDKChatModel`` shim implements LangChain's ``BaseChatModel``
so existing callers (scorer, CV compression, web search) work unchanged.
"""
import asyncio
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Coroutine, List, Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from providers.llm import usage_tracker
from providers.llm.base import BaseLLMProvider
from providers.llm.retry import backoff_seconds, is_auth_error

logger = logging.getLogger(__name__)


# Infisical secret holding the API key for ``billing: api``. Deliberately NOT
# named ANTHROPIC_API_KEY — see module docstring.
API_KEY_SECRET = "AJSAA_ANTHROPIC_API_KEY"

VALID_BILLING = ("subscription", "api")

# Built-in Claude Code tools granted to search tasks. Nothing else is allowed.
SEARCH_TOOLS = ["WebSearch", "WebFetch"]

# Used when the caller supplies no system message. Replaces Claude Code's long
# default coding-assistant prompt, which would add tokens (and cost on API
# billing) to every call for no benefit here.
DEFAULT_SYSTEM_PROMPT = (
    "You are a precise component of an automated job-search pipeline. "
    "Follow the requested output format exactly and add nothing else."
)

# Same control-character filter as claude_code_provider / llm_scorer, so
# prompts are byte-identical across providers.
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


# ── Billing ──────────────────────────────────────────────────────────────────

def build_sdk_env(billing: str) -> dict[str, str]:
    """Return the env overrides that pin the SDK subprocess to one billing source.

    Raises:
        ValueError: unknown billing mode.
        RuntimeError: ``api`` mode without the API-key secret.
    """
    if billing == "api":
        key = os.environ.get(API_KEY_SECRET, "")
        if not key:
            raise RuntimeError(
                f"llm.billing=api but {API_KEY_SECRET} is not set. "
                f"Add it to Infisical, or switch llm.billing to 'subscription'."
            )
        return {"ANTHROPIC_API_KEY": key}

    if billing == "subscription":
        # The subprocess inherits os.environ; an inherited ANTHROPIC_API_KEY
        # would win over the logged-in session. Blank it explicitly.
        if os.environ.get("ANTHROPIC_API_KEY"):
            logger.warning(
                "ANTHROPIC_API_KEY is set but llm.billing=subscription — "
                "blanking it for Agent SDK calls so they bill your subscription",
            )
        return {"ANTHROPIC_API_KEY": ""}

    raise ValueError(f"Unknown llm.billing '{billing}'. Supported: {', '.join(VALID_BILLING)}")


# ── Options ──────────────────────────────────────────────────────────────────

def build_options(
    *,
    model: str,
    system_prompt: str,
    allow_tools: bool,
    max_turns: int,
    billing: str,
) -> Any:
    """Build ``ClaudeAgentOptions`` for one pipeline call.

    ``tools`` restricts which built-in tools exist at all; ``allowed_tools``
    pre-approves them (there is no human to answer a permission prompt).
    ``max_turns`` only matters when tools are on — without tools the model
    answers in a single turn.
    """
    from claude_agent_sdk import ClaudeAgentOptions

    tools = list(SEARCH_TOOLS) if allow_tools else []
    return ClaudeAgentOptions(
        model=model or None,
        system_prompt=system_prompt,
        tools=tools,
        allowed_tools=tools,
        setting_sources=[],
        skills=[],
        strict_mcp_config=True,
        max_turns=max_turns if allow_tools else None,
        env=build_sdk_env(billing),
    )


# ── Execution ────────────────────────────────────────────────────────────────

def run_blocking(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run a coroutine to completion from synchronous code.

    Normally that's ``asyncio.run``. When an event loop is already running in
    this thread (e.g. inside an Agent SDK tool handler in M2), ``asyncio.run``
    would raise — so we run it on a short-lived worker thread instead.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


async def _collect_result(prompt: str, options: Any) -> Any:
    """Drive one ``query()`` call and return its final ``ResultMessage`` (or None)."""
    from claude_agent_sdk import ResultMessage, query

    result = None
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, ResultMessage):
            result = message
    return result


def _interpret_result(result: Any) -> tuple[Optional[str], Optional[str]]:
    """Return ``(content, error_message)`` — exactly one of them is set."""
    if result is None:
        return None, "Agent SDK returned no result message"
    if result.is_error:
        detail = result.errors or result.result or "(no message)"
        return None, f"Agent SDK error subtype={result.subtype} status={result.api_error_status} detail={detail}"
    text = (result.result or "").strip()
    if not text:
        return None, f"Agent SDK returned empty result (subtype={result.subtype})"
    return text, None


def _record_usage(result: Any, model_label: str) -> None:
    """Push the call's tokens and cost into the shared usage tracker.

    Costs are computed by Claude Code itself, so they are correct on API
    billing and an API-equivalent estimate on subscription billing — same
    semantics as the legacy CLI provider. Never raises.

    One SDK query can involve more than one model: Claude Code makes a small
    housekeeping call on its own small model alongside ours. ``model_usage``
    breaks the cost down per model, so we record each one separately; the
    aggregate ``usage`` is only the fallback when that breakdown is missing.
    """
    try:
        per_model = result.model_usage or {}
        if per_model:
            for model, u in per_model.items():
                usage_tracker.record(
                    model,
                    {
                        "input_tokens": int(u.get("inputTokens", 0) or 0),
                        "output_tokens": int(u.get("outputTokens", 0) or 0),
                        "cache_read_input_tokens": int(u.get("cacheReadInputTokens", 0) or 0),
                        "cache_creation_input_tokens": int(u.get("cacheCreationInputTokens", 0) or 0),
                    },
                    float(u.get("costUSD", 0.0) or 0.0),
                )
            return

        usage = result.usage or {}
        usage_tracker.record(
            model_label,
            {
                "input_tokens": int(usage.get("input_tokens", 0) or 0),
                "output_tokens": int(usage.get("output_tokens", 0) or 0),
                "cache_read_input_tokens": int(usage.get("cache_read_input_tokens", 0) or 0),
                "cache_creation_input_tokens": int(usage.get("cache_creation_input_tokens", 0) or 0),
            },
            float(result.total_cost_usd or 0.0),
        )
    except Exception as exc:  # pragma: no cover — observability must never break the call
        logger.debug("usage capture failed: %s", exc)


def run_prompt(
    prompt: str,
    *,
    options: Any,
    timeout: int,
    model_label: str,
    retries: int = 2,
) -> str:
    """Send one prompt through the Agent SDK, retrying transient failures.

    Retries use the shared backoff schedule (rate-limit windows need time to
    refill). Auth errors abort immediately — retrying cannot fix them.

    Raises:
        RuntimeError: SDK/CLI missing, auth failure, or every attempt failed.
    """
    from claude_agent_sdk import CLINotFoundError

    prompt = _CONTROL_CHAR_RE.sub("", prompt)
    total_attempts = retries + 1
    last_error: Exception = RuntimeError("No attempts made")

    for attempt in range(1, total_attempts + 1):
        wait = backoff_seconds(attempt)
        if wait > 0:
            logger.info("Agent SDK: backing off %.1fs before attempt %d/%d", wait, attempt, total_attempts)
            time.sleep(wait)

        err_msg: Optional[str]
        try:
            result = run_blocking(asyncio.wait_for(_collect_result(prompt, options), timeout))
        except CLINotFoundError as exc:
            # Permanent: the SDK's bundled CLI is missing — retrying won't help.
            raise RuntimeError(f"Claude Code CLI not available to the Agent SDK: {exc}") from exc
        except Exception as exc:
            # ProcessError, timeouts, JSON decode errors → treat as transient.
            err_msg = f"{type(exc).__name__}: {exc}"
        else:
            content, err_msg = _interpret_result(result)
            if content is not None:
                _record_usage(result, model_label)
                return content

        last_error = RuntimeError(err_msg or "Agent SDK call failed with unknown error")
        logger.warning("Agent SDK attempt %d/%d failed: %s", attempt, total_attempts, err_msg)

        if err_msg and is_auth_error(err_msg):
            logger.error("Agent SDK auth error — aborting retries (check 'claude /login' or the API key)")
            raise last_error

    raise last_error


# ── LangChain shim ───────────────────────────────────────────────────────────

def split_messages(messages: List[BaseMessage]) -> tuple[str, str]:
    """Split LangChain messages into ``(system_prompt, prompt)``.

    System messages become the SDK system prompt (falling back to
    ``DEFAULT_SYSTEM_PROMPT``). Human/AI messages are flattened into one
    prompt with an ``[Assistant]`` header for prior AI turns, as before.
    """
    system_parts: list[str] = []
    prompt_parts: list[str] = []
    for msg in messages:
        role = getattr(msg, "type", "human")
        content = str(msg.content)
        if role == "system":
            system_parts.append(content)
        elif role == "ai":
            prompt_parts.append(f"[Assistant]\n{content}")
        else:
            prompt_parts.append(content)
    system_prompt = "\n\n".join(system_parts) or DEFAULT_SYSTEM_PROMPT
    return system_prompt, "\n\n".join(prompt_parts)


class ClaudeAgentSDKChatModel(BaseChatModel):
    """LangChain-compatible model that routes calls through the Agent SDK."""

    timeout: int = 120
    model: str = ""
    allow_tools: bool = False
    max_turns: int = 10
    billing: str = "subscription"

    @property
    def _llm_type(self) -> str:
        return "claude_agent_sdk"

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Required ``BaseChatModel`` hook — one SDK query per invoke."""
        system_prompt, prompt = split_messages(messages)
        options = build_options(
            model=self.model,
            system_prompt=system_prompt,
            allow_tools=self.allow_tools,
            max_turns=self.max_turns,
            billing=self.billing,
        )
        content = run_prompt(
            prompt,
            options=options,
            timeout=self.timeout,
            model_label=self.model or "claude_agent_sdk",
        )
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])


class ClaudeAgentSDKProvider(BaseLLMProvider):
    """Provider adapter that builds a :class:`ClaudeAgentSDKChatModel`."""

    def __init__(self, cfg: dict) -> None:
        self.timeout = cfg.get("timeout", 120)
        self.model = cfg.get("model", "")
        # Set by the factory: True only for task="search".
        self.allow_tools = cfg.get("allow_tools", False)
        self.max_turns = cfg.get("search_max_turns", 10)
        self.billing = cfg.get("billing", "subscription")

    def build(self) -> ClaudeAgentSDKChatModel:
        # Validate billing now so a missing API key fails at startup,
        # not halfway through a run.
        build_sdk_env(self.billing)
        logger.info(
            "ClaudeAgentSDKProvider: billing=%s model=%s tools=%s",
            self.billing, self.model or "default", SEARCH_TOOLS if self.allow_tools else "none",
        )
        return ClaudeAgentSDKChatModel(
            timeout=self.timeout,
            model=self.model,
            allow_tools=self.allow_tools,
            max_turns=self.max_turns,
            billing=self.billing,
        )
