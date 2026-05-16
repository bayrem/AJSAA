"""Anthropic Claude provider — pay-per-token via the Anthropic API.

Uses ``langchain-anthropic``'s ``ChatAnthropic`` adapter. Costs are billed
to the API key under ``ANTHROPIC_API_KEY``; if you have a Claude Pro/Max
subscription and want to use those tokens instead, use the
``claude_code_agent`` provider which shells out to the ``claude`` CLI.
"""
import os

from providers.llm.base import BaseLLMProvider


class AnthropicProvider(BaseLLMProvider):
    """Direct API access via ``langchain-anthropic``."""

    def __init__(self, cfg: dict) -> None:
        # Model is set by the factory based on task — see
        # providers/llm/factory.py for the routing logic.
        self.model = cfg.get("model", "claude-sonnet-4-6")
        self.max_tokens = cfg.get("max_tokens", 4096)
        # temperature=0 by default — deterministic output matters more than
        # creative variation for both scoring and search.
        self.temperature = cfg.get("temperature", 0)
        self.api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    def build(self):
        # Lazy import — `langchain_anthropic` is a heavy dep that we don't
        # want to pay for when the user chose a different provider.
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            anthropic_api_key=self.api_key,
        )
