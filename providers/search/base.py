from abc import ABC, abstractmethod


class BaseSearchProvider(ABC):
    @abstractmethod
    def search(self, query: str, max_results: int = 10, **kwargs) -> list[dict]:
        """Return list of job dicts with at least: title, company, location, url, description."""
