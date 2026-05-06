from abc import ABC, abstractmethod


class BaseStorageProvider(ABC):
    @abstractmethod
    def save(self, jobs: list[dict]) -> int:
        """Persist jobs. Return count of newly added (deduplicated) jobs."""

    @abstractmethod
    def load_all(self) -> list[dict]:
        """Return all stored jobs."""
