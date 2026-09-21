"""Telegram Bot notification channel implementation."""

import logging
import httpx
from .base import BaseNotifier

logger = logging.getLogger(__name__)


class TelegramNotifier(BaseNotifier):
    """Notifier for Telegram Bot using the official Bot API."""

    def __init__(self, bot_token: str | None, chat_id: str | None, enabled: bool = True):
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._enabled = enabled and bool(bot_token and chat_id)
        self._base_url = f"https://api.telegram.org/bot{bot_token}" if bot_token else None

    @property
    def name(self) -> str:
        return "telegram"

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    async def send_message(self, title: str, message: str, level: str = "info") -> bool:
        if not self.is_enabled or not self._base_url or not self._chat_id:
            return False

        icon = {
            "info": "ℹ️",
            "warning": "⚠️",
            "success": "✅",
            "error": "🚨",
        }.get(level, "🔔")

        text = f"{icon} <b>{title}</b>\n\n{message}"

        url = f"{self._base_url}/sendMessage"
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    logger.info("Telegram notification sent: %s", title)
                    return True
                logger.error("Failed to send Telegram notification: HTTP %s - %s", resp.status_code, resp.text)
                return False
        except Exception as e:
            logger.exception("Error sending Telegram message: %s", e)
            return False
