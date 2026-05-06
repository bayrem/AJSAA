from abc import ABC, abstractmethod


class BaseNotifier(ABC):
    @abstractmethod
    def send(self, message: str, html_body: str | None = None) -> None:
        """Send a notification message. html_body is the rich version (email only)."""
