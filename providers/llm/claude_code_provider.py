"""LLM provider that shells out to the Claude Code CLI.

Routes every completion through ``claude -p "<prompt>" --output-format json``.
The advantage over ``anthropic_provider`` is billing: completions go against
the user's Claude Pro/Max subscription rather than the Anthropic API budget.
No API key is required — just an authenticated ``claude`` CLI on the PATH.

The provider exposes a custom ``ClaudeCodeChatModel`` that satisfies the
LangChain ``BaseChatModel`` interface, so the rest of the codebase doesn't
need to know it's not talking to a normal API model.

The CLI is invoked with ``--output-format json`` so we can read structured
error information (subtype, message) when a call fails. On rate-limit or
other transient failure we retry with exponential backoff + jitter so a
per-minute rate-limit window has time to refill before the next attempt.
Authentication errors abort immediately because retrying won't help.

Required setup:
  - ``claude`` CLI installed (https://claude.ai/code)
  - User authenticated (``claude /login``)
"""
import json
import logging
import random
import re
import shutil
import subprocess
import time
from typing import Any, List, Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from providers.llm import usage_tracker
from providers.llm.base import BaseLLMProvider

logger = logging.getLogger(__name__)


# Control character regex — same as in llm_scorer._sanitise. Kept identical
# so prompts hashed across the two codepaths produce the same bytes.
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


# Per-attempt backoff schedule (seconds). Index 0 = wait before attempt 2,
# index 1 = wait before attempt 3, etc. Tuned to the ~65s recovery window
# observed for the Claude Code subscription's per-minute Sonnet rate limit
# in issue #58 — three retries (5 + 15 + 45 = 65s) cover the worst case.
_BACKOFF_SECONDS = (5.0, 15.0, 45.0)
_JITTER_PCT = 0.2  # ±20% on each delay to avoid synchronised retry storms

# Substrings (case-insensitive) we look for in CLI error messages to decide
# whether to keep retrying. Auth errors are permanent — no point burning the
# retry budget. Other errors are treated as transient by default.
_AUTH_ERROR_HINTS = (
    "authentication",
    "unauthorized",
    "invalid api key",
    "auth_",
    "login required",
    "not logged in",
)


def _sanitise(text: str, max_chars: int = 2000) -> str:
    """Strip control characters and cap length before passing to the CLI.

    This is a last-resort defence against prompts containing unprintable
    bytes that could confuse the CLI's argument parser. Most callers have
    already sanitised their inputs.
    """
    text = _CONTROL_CHAR_RE.sub("", text)
    return text[:max_chars]


class ClaudeCodeChatModel(BaseChatModel):
    """LangChain-compatible model that routes calls through the ``claude`` CLI."""

    # Defaults are sized for typical scoring/compression prompts. 120s is
    # generous enough for the model to do tool calls if allow_tools=True.
    timeout: int = 120
    model_name: str = "claude_code_agent"
    model: str = ""              # passed via --model when truthy
    allow_tools: bool = True     # see ClaudeCodeProvider docstring

    @property
    def _llm_type(self) -> str:
        return "claude_code_agent"

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Required hook for ``BaseChatModel`` — converts messages → CLI call."""
        prompt = self._messages_to_prompt(messages)
        content = _invoke_claude_cli(
            prompt, timeout=self.timeout, model=self.model, allow_tools=self.allow_tools
        )
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])

    def _messages_to_prompt(self, messages: List[BaseMessage]) -> str:
        """Flatten a LangChain message list into a single prompt string.

        The CLI takes only one ``-p`` value so we render multi-message
        conversations as inline ``[System]`` / ``[Assistant]`` headers
        followed by the message body.
        """
        parts = []
        for msg in messages:
            role = getattr(msg, "type", "human")
            content = str(msg.content)
            if role == "system":
                parts.append(f"[System]\n{content}")
            elif role == "ai":
                parts.append(f"[Assistant]\n{content}")
            else:
                # "human" is the common case — no header needed
                parts.append(content)
        return "\n\n".join(parts)


def _backoff_seconds(attempt: int) -> float:
    """Return seconds to sleep before the given attempt (1-indexed).

    Attempt 1 has no wait. Attempts 2+ pull from ``_BACKOFF_SECONDS`` with
    ±20% jitter. Attempts beyond the schedule reuse the last entry. Returns
    0 for attempt 1 so callers can use a uniform ``if wait > 0: sleep(wait)``
    pattern.
    """
    if attempt <= 1:
        return 0.0
    idx = min(attempt - 2, len(_BACKOFF_SECONDS) - 1)
    base = _BACKOFF_SECONDS[idx]
    jitter = base * _JITTER_PCT
    return base + random.uniform(-jitter, jitter)


def _parse_cli_response(
    stdout: str, stderr: str, returncode: int
) -> tuple[Optional[str], Optional[str], bool]:
    """Inspect a completed CLI run and return ``(content, error_msg, is_auth_error)``.

    - ``content``: the response text if the call succeeded; ``None`` on failure.
    - ``error_msg``: human-readable failure description for logs; ``None`` on success.
    - ``is_auth_error``: ``True`` if the CLI signalled a permanent auth problem —
      the caller should not retry.

    Resilience strategy: try JSON first (the canonical format with
    ``--output-format json``). If JSON parsing fails — e.g. the CLI exited
    before emitting any output, which is the rate-limit signature observed
    in issue #58 — fall back to ``(returncode, stdout, stderr)`` heuristics.
    """
    data: Any = None
    if stdout:
        try:
            data = json.loads(stdout)
        except (json.JSONDecodeError, ValueError):
            data = None

    if isinstance(data, dict):
        if not data.get("is_error", False):
            result = data.get("result", "")
            if isinstance(result, str) and result.strip():
                return (result.strip(), None, False)
            subtype = data.get("subtype") or "(none)"
            return (None, f"CLI returned empty result (subtype={subtype})", False)

        subtype = str(data.get("subtype") or data.get("error_type") or "unknown")
        msg = str(data.get("error") or data.get("message") or "")
        signal = f"{subtype} {msg}".lower()
        is_auth = any(hint in signal for hint in _AUTH_ERROR_HINTS)
        return (
            None,
            f"CLI error subtype={subtype} msg={msg or '(no message)'}",
            is_auth,
        )

    # No JSON parsable from stdout — fall back to legacy text-mode interpretation.
    # This path also covers the observed rate-limit case: exit code 1 with no
    # output at all on either stream.
    if returncode == 0 and stdout.strip():
        return (stdout.strip(), None, False)

    err_text = stderr.strip() or "(no output)"
    return (None, f"CLI exited with code {returncode}: {err_text}", False)


def _extract_usage_from_cli_json(stdout: str) -> tuple[str, dict, float] | None:
    """Return ``(model, canonical_usage, total_cost_usd)`` from a CLI JSON blob.

    The CLI emits a top-level ``usage`` dict plus a top-level ``total_cost_usd``
    field, and (on recent versions) a ``model`` field. We translate the
    SDK-specific usage keys to the canonical shape used by
    ``usage_tracker.record``. Returns ``None`` if the stdout isn't valid JSON
    or the ``usage`` block is missing — callers should skip recording.

    The Claude Code CLI's ``usage`` block uses the same key names as the
    Anthropic Messages API: ``input_tokens``, ``output_tokens``,
    ``cache_read_input_tokens``, ``cache_creation_input_tokens``.
    """
    if not stdout:
        return None
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None

    usage = data.get("usage")
    if not isinstance(usage, dict):
        return None

    # Cost may be missing on older CLI versions — default to 0.0 so the
    # call still records (tokens are still useful even if cost is unknown).
    cost = data.get("total_cost_usd", 0.0)
    try:
        cost_f = float(cost)
    except (TypeError, ValueError):
        cost_f = 0.0

    # Prefer the model name reported by the CLI itself. If the CLI didn't echo
    # a model field, the caller will pass through the configured model string.
    model = str(data.get("model") or "")

    return (
        model,
        {
            "input_tokens": int(usage.get("input_tokens", 0) or 0),
            "output_tokens": int(usage.get("output_tokens", 0) or 0),
            "cache_read_input_tokens": int(usage.get("cache_read_input_tokens", 0) or 0),
            "cache_creation_input_tokens": int(usage.get("cache_creation_input_tokens", 0) or 0),
        },
        cost_f,
    )


def _invoke_claude_cli(
    prompt: str,
    timeout: int = 120,
    retries: int = 2,
    model: str = "",
    allow_tools: bool = True,
) -> str:
    """Run the ``claude`` CLI with a sanitised prompt; retry on transient failure.

    Each call passes ``--output-format json`` so error information is recoverable
    when the CLI fails. Transient failures (rate limit, empty output) are
    retried with exponential backoff. Auth errors abort immediately because
    retrying a permanent failure just burns the retry budget.

    Args:
        prompt: User prompt sent via ``-p``. Sanitised to strip control chars
            and capped at 50k characters.
        timeout: Per-attempt timeout in seconds.
        retries: Number of *additional* attempts after the first failure
            (total attempts = retries + 1).
        model: When non-empty, passed via ``--model`` so multi-model routing
            works the same way it does for the API providers.
        allow_tools: When True, passes ``--dangerously-skip-permissions`` so
            the CLI can invoke tools without prompting. Set False for scoring
            or compression where tools aren't needed (faster, safer).

    Returns:
        The response text from a successful CLI run.

    Raises:
        RuntimeError: If the CLI isn't on PATH, every retry exhausts, or an
            auth error is detected (no retries on auth errors).
    """
    claude_bin = shutil.which("claude")
    if not claude_bin:
        raise RuntimeError(
            "'claude' CLI not found in PATH. "
            "Install Claude Code: https://claude.ai/code"
        )

    # Cap at 50k chars — the CLI accepts much more but Python's argv has
    # platform-dependent limits and 50k is plenty for typical scoring batches.
    prompt = _sanitise(prompt, max_chars=50_000)
    cmd = [claude_bin]
    if allow_tools:
        cmd.append("--dangerously-skip-permissions")
    cmd.extend(["-p", prompt, "--output-format", "json"])
    if model:
        cmd.extend(["--model", model])

    total_attempts = retries + 1
    last_error: Exception = RuntimeError("No attempts made")

    for attempt in range(1, total_attempts + 1):
        wait = _backoff_seconds(attempt)
        if wait > 0:
            logger.info(
                "claude CLI: backing off %.1fs before attempt %d/%d",
                wait, attempt, total_attempts,
            )
            time.sleep(wait)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        content, err_msg, is_auth = _parse_cli_response(
            result.stdout, result.stderr, result.returncode,
        )

        if content is not None:
            # Capture token usage and dollar cost from the CLI's JSON payload.
            # The CLI bills us directly (via the user's Pro/Max subscription),
            # so we use its ``total_cost_usd`` rather than the price table.
            # Failures here are swallowed — observability must never break
            # the actual LLM call path.
            try:
                extracted = _extract_usage_from_cli_json(result.stdout)
                if extracted is not None:
                    cli_model, usage_dict, cost = extracted
                    # Fall back to the configured model name if the CLI
                    # didn't echo one (older CLI versions).
                    usage_tracker.record(cli_model or model or "claude_code_agent", usage_dict, cost)
            except Exception as exc:  # pragma: no cover — defensive only
                logger.debug("usage capture failed: %s", exc)
            return content

        last_error = RuntimeError(err_msg or "claude CLI failed with unknown error")
        logger.warning(
            "claude CLI attempt %d/%d failed: %s", attempt, total_attempts, err_msg,
        )

        if is_auth:
            logger.error(
                "claude CLI auth error — aborting retries (run 'claude /login')",
            )
            raise last_error

    raise last_error


class ClaudeCodeProvider(BaseLLMProvider):
    """Provider adapter that builds a :class:`ClaudeCodeChatModel`."""

    def __init__(self, cfg: dict) -> None:
        self.timeout = cfg.get("timeout", 120)
        self.model = cfg.get("model", "")
        # ``allow_tools: false`` in config disables the CLI's tool-use
        # permission flag — recommended for scoring/compression tasks that
        # never need to call MCP tools.
        self.allow_tools = cfg.get("allow_tools", True)

    def build(self) -> ClaudeCodeChatModel:
        # Sanity-check the CLI early so misconfigured deployments fail at
        # startup rather than at the first model invocation.
        claude_bin = shutil.which("claude")
        if not claude_bin:
            raise RuntimeError(
                "'claude' CLI not found in PATH. "
                "Install Claude Code: https://claude.ai/code"
            )
        logger.info(
            "ClaudeCodeProvider: using claude CLI at %s (model=%s, allow_tools=%s)",
            claude_bin, self.model or "default", self.allow_tools,
        )
        return ClaudeCodeChatModel(
            timeout=self.timeout, model=self.model, allow_tools=self.allow_tools
        )
