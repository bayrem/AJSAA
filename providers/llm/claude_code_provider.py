"""LLM provider that routes calls through the Claude Code CLI.

Uses `claude -p "<prompt>"` subprocess so all completions are billed
against the user's Claude Pro/Max subscription — no Anthropic API credits needed.
Requires the `claude` CLI to be installed and authenticated.
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


def _sanitise(text: str, max_chars: int = 2000) -> str:
    """Strip control characters and truncate. Last-resort defence before CLI injection."""
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return text[:max_chars]


class ClaudeCodeChatModel(BaseChatModel):
    timeout: int = 120
    model_name: str = "claude_code_agent"
    model: str = ""       # passed to --model flag when set
    allow_tools: bool = True  # when False, omits --dangerously-skip-permissions

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
        prompt = self._messages_to_prompt(messages)
        content = _invoke_claude_cli(prompt, timeout=self.timeout, model=self.model, allow_tools=self.allow_tools)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])

    def _messages_to_prompt(self, messages: List[BaseMessage]) -> str:
        parts = []
        for msg in messages:
            role = getattr(msg, "type", "human")
            content = str(msg.content)
            if role == "system":
                parts.append(f"[System]\n{content}")
            elif role == "ai":
                parts.append(f"[Assistant]\n{content}")
            else:
                parts.append(content)
        return "\n\n".join(parts)


def _invoke_claude_cli(
    prompt: str,
    timeout: int = 120,
    retries: int = 2,
    model: str = "",
    allow_tools: bool = True,
) -> str:
    claude_bin = shutil.which("claude")
    if not claude_bin:
        raise RuntimeError(
            "'claude' CLI not found in PATH. "
            "Install Claude Code: https://claude.ai/code"
        )

    prompt = _sanitise(prompt, max_chars=50_000)
    cmd = [claude_bin]
    if allow_tools:
        cmd.append("--dangerously-skip-permissions")
    cmd.extend(["-p", prompt])
    if model:
        cmd.extend(["--model", model])

    last_error: Exception = RuntimeError("No attempts made")
    for attempt in range(1, retries + 2):
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        if result.returncode != 0:
            last_error = RuntimeError(
                f"claude CLI exited with code {result.returncode}:\n{result.stderr.strip()}"
            )
            logger.warning("claude CLI attempt %d/%d failed: %s", attempt, retries + 1, last_error)
            continue

        content = result.stdout.strip()
        if not content:
            last_error = RuntimeError("claude CLI returned empty output")
            logger.warning("claude CLI attempt %d/%d returned empty output", attempt, retries + 1)
            continue

        return content

    raise last_error


class ClaudeCodeProvider(BaseLLMProvider):
    def __init__(self, cfg: dict):
        self.timeout = cfg.get("timeout", 120)
        self.model = cfg.get("model", "")
        # Set allow_tools: false in config for scoring/compression tasks that don't need tool access.
        self.allow_tools = cfg.get("allow_tools", True)

    def build(self) -> ClaudeCodeChatModel:
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
        return ClaudeCodeChatModel(timeout=self.timeout, model=self.model, allow_tools=self.allow_tools)
