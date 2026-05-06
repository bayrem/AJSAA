"""LLM provider that routes calls through the Claude Code CLI.

Uses `claude -p "<prompt>"` subprocess so all completions are billed
against the user's Claude Pro/Max subscription — no Anthropic API credits needed.
Requires the `claude` CLI to be installed and authenticated.
"""
import shutil
import subprocess
import logging
from typing import Any, List, Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from providers.llm.base import BaseLLMProvider

logger = logging.getLogger(__name__)


class ClaudeCodeChatModel(BaseChatModel):
    timeout: int = 120
    model_name: str = "claude_code_agent"

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
        content = _invoke_claude_cli(prompt, timeout=self.timeout)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])

    def _messages_to_prompt(self, messages: List[BaseMessage]) -> str:
        parts = []
        for msg in messages:
            role = getattr(msg, "type", "human")
            if role == "system":
                parts.append(f"[System]\n{msg.content}")
            elif role == "ai":
                parts.append(f"[Assistant]\n{msg.content}")
            else:
                parts.append(msg.content)
        return "\n\n".join(parts)


def _invoke_claude_cli(prompt: str, timeout: int = 120, retries: int = 2) -> str:
    claude_bin = shutil.which("claude")
    if not claude_bin:
        raise RuntimeError(
            "'claude' CLI not found in PATH. "
            "Install Claude Code: https://claude.ai/code"
        )

    last_error: Exception = RuntimeError("No attempts made")
    for attempt in range(1, retries + 2):
        result = subprocess.run(
            [claude_bin, "--dangerously-skip-permissions", "-p", prompt],
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

    def build(self) -> ClaudeCodeChatModel:
        claude_bin = shutil.which("claude")
        if not claude_bin:
            raise RuntimeError(
                "'claude' CLI not found in PATH. "
                "Install Claude Code: https://claude.ai/code"
            )
        logger.info("ClaudeCodeProvider: using claude CLI at %s", claude_bin)
        return ClaudeCodeChatModel(timeout=self.timeout)
