import os
from providers.llm.base import BaseLLMProvider


class AnthropicProvider(BaseLLMProvider):
    def __init__(self, cfg: dict):
        self.model = cfg.get("model", "claude-sonnet-4-6")
        self.max_tokens = cfg.get("max_tokens", 4096)
        self.temperature = cfg.get("temperature", 0)
        self.api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    def build(self):
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            anthropic_api_key=self.api_key,
        )
