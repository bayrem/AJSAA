from abc import ABC, abstractmethod


class BaseLLMProvider(ABC):
    """Marker base class. Concrete providers return a LangChain BaseChatModel."""

    @abstractmethod
    def build(self) -> object:
        """Return a langchain_core.language_models.BaseChatModel instance."""
