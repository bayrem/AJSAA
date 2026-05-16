"""LLM provider that shells out to the Claude Code CLI.

Routes every completion through ``claude -p "<prompt>"``. The advantage over
``anthropic_provider`` is billing: completions go against the user's Claude
Pro/Max subscription rather than the Anthropic API budget. No API key is
required — just an authenticated ``claude`` CLI on the PATH.

The provider exposes a custom ``ClaudeCodeChatModel`` that satisfies the
LangChain ``BaseChatModel`` interface, so the rest of the codebase doesn't
need to know it's not talking to a normal API model.

Required setup:
  - ``claude`` CLI installed (https://claude.ai/code)
  - User authenticated (``claude /login``)
"""
import logging
import re
import shutil
import subprocess
from typing import Any, List, Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from providers.llm.base import BaseLLMProvider

logger = logging.getLogger(__name__)


# Control character regex — same as in llm_scorer._sanitise. Kept identical
# so prompts hashed across the two codepaths produce the same bytes.
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


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


def _invoke_claude_cli(
    prompt: str,
    timeout: int = 120,
    retries: int = 2,
    model: str = "",
    allow_tools: bool = True,
) -> str:
    """Run the ``claude`` CLI with a sanitised prompt; retry on transient failure.

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
        The stdout content from a successful CLI run.

    Raises:
        RuntimeError: If the CLI isn't on PATH, or every attempt fails.
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
    cmd.extend(["-p", prompt])
    if model:
        cmd.extend(["--model", model])

    last_error: Exception = RuntimeError("No attempts made")
    for attempt in range(1, retries + 2):
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

        if result.returncode != 0:
            # Non-zero exit — capture stderr and try again. Common causes:
            # transient API throttling, auth token expiry.
            last_error = RuntimeError(
                f"claude CLI exited with code {result.returncode}:\n{result.stderr.strip()}"
            )
            logger.warning("claude CLI attempt %d/%d failed: %s", attempt, retries + 1, last_error)
            continue

        content = result.stdout.strip()
        if not content:
            # Zero exit but no output — rare but seen during heavy load.
            last_error = RuntimeError("claude CLI returned empty output")
            logger.warning("claude CLI attempt %d/%d returned empty output", attempt, retries + 1)
            continue

        return content

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
