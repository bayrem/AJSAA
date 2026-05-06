from abc import ABC, abstractmethod


class BaseJobBoardConnector(ABC):
    def __init__(self, cfg: dict):
        self.cfg = cfg

    @abstractmethod
    def search(self, query: str, max_results: int = 10, **kwargs) -> list[dict]:
        """Return job postings from this board."""
