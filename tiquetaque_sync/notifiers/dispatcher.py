"""Notification Dispatcher combining all active notification channels."""

import logging
from .base import BaseNotifier
from .telegram import TelegramNotifier
from .slack import SlackNotifier

logger = logging.getLogger(__name__)


class NotificationDispatcher:
    """Dispatches notifications across all configured and enabled channels."""

    def __init__(self, notifiers: list[BaseNotifier] | None = None):
        self.notifiers: list[BaseNotifier] = notifiers or []

    def register(self, notifier: BaseNotifier) -> None:
        self.notifiers.append(notifier)

    async def dispatch(self, title: str, message: str, level: str = "info") -> dict[str, bool]:
        """Dispatch message to all enabled notifiers.

        Returns:
            dict mapping channel name to delivery success (True/False).
        """
        results = {}
        for notifier in self.notifiers:
            if notifier.is_enabled:
                success = await notifier.send_message(title=title, message=message, level=level)
                results[notifier.name] = success
            else:
                results[notifier.name] = False

        logger.debug("Dispatched '%s' - results: %s", title, results)
        return results


def create_dispatcher_from_settings(settings) -> NotificationDispatcher:
    """Factory creating dispatcher populated from application settings."""
    dispatcher = NotificationDispatcher()

    telegram = TelegramNotifier(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
        enabled=settings.telegram_enabled,
    )
    dispatcher.register(telegram)

    slack = SlackNotifier(
        webhook_url=settings.slack_webhook_url,
        enabled=settings.slack_enabled,
    )
    dispatcher.register(slack)

    return dispatcher
