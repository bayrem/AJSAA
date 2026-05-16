"""Base contract for LLM providers.

A "provider" is the small adapter class responsible for turning a config
dict into a LangChain ``BaseChatModel``. The chat model itself is what every
node consumes — providers are only used in ``providers/llm/factory.py``.
"""
from abc import ABC, abstractmethod


class BaseLLMProvider(ABC):
    """Adapter from config-dict to LangChain chat model.

    Subclasses store any provider-specific options (model name, temperature,
    timeout, etc.) on ``self`` in ``__init__`` and instantiate the actual
    chat model only in ``build()``. This split keeps construction cheap and
    defers any side-effecting work (e.g. checking that a CLI binary exists)
    until the model is actually needed.
    """

    @abstractmethod
    def build(self) -> object:
        """Return a configured ``langchain_core.language_models.BaseChatModel``."""
