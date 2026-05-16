"""OpenAI provider — pay-per-token via the OpenAI API.

Available as an alternative when the user prefers GPT-family models. Used
by setting ``provider: openai`` in config.yaml.
"""
import os

from providers.llm.base import BaseLLMProvider


class OpenAIProvider(BaseLLMProvider):
    """Direct API access via ``langchain-openai``."""

    def __init__(self, cfg: dict) -> None:
        self.model = cfg.get("model", "gpt-4o")
        self.max_tokens = cfg.get("max_tokens", 4096)
        # Deterministic output by default — see notes in anthropic_provider.
        self.temperature = cfg.get("temperature", 0)
        self.api_key = os.environ.get("OPENAI_API_KEY", "")

    def build(self):
        # Lazy import — keeps langchain_openai off the import path when
        # another provider is selected.
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            openai_api_key=self.api_key,
        )
