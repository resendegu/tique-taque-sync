"""Abstract base class for notification channels."""

from abc import ABC, abstractmethod


class BaseNotifier(ABC):
    """Base interface for all notification channel implementations."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Name of the notification channel."""
        pass

    @property
    @abstractmethod
    def is_enabled(self) -> bool:
        """Check if this notification channel is configured and enabled."""
        pass

    @abstractmethod
    async def send_message(self, title: str, message: str, level: str = "info") -> bool:
        """Send a notification message.

        Args:
            title: Short header or subject
            message: Body text (may include markdown)
            level: 'info', 'warning', 'success', 'error'
        """
        pass
