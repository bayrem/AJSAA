import os

from providers.llm.base import BaseLLMProvider


class OpenAIProvider(BaseLLMProvider):
    def __init__(self, cfg: dict):
        self.model = cfg.get("model", "gpt-4o")
        self.max_tokens = cfg.get("max_tokens", 4096)
        self.temperature = cfg.get("temperature", 0)
        self.api_key = os.environ.get("OPENAI_API_KEY", "")

    def build(self):
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            openai_api_key=self.api_key,
        )
