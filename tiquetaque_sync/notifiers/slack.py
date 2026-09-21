import re
import logging
import httpx
from .base import BaseNotifier

logger = logging.getLogger(__name__)


def html_to_mrkdwn(text: str) -> str:
    """Convert HTML formatting tags (<b>, <i>, <code>, etc.) into Slack mrkdwn format."""
    if not text:
        return ""
    # Convert bold <b>...</b> and <strong>...</strong> to *...*
    text = re.sub(r"<\s*(?:b|strong)\s*>(.*?)<\s*/\s*(?:b|strong)\s*>", r"*\1*", text, flags=re.IGNORECASE | re.DOTALL)
    # Convert italic <i>...</i> and <em>...</em> to _..._
    text = re.sub(r"<\s*(?:i|em)\s*>(.*?)<\s*/\s*(?:i|em)\s*>", r"_\1_", text, flags=re.IGNORECASE | re.DOTALL)
    # Convert strikethrough <s>...</s> to ~...~
    text = re.sub(r"<\s*(?:s|strike|del)\s*>(.*?)<\s*/\s*(?:s|strike|del)\s*>", r"~\1~", text, flags=re.IGNORECASE | re.DOTALL)
    # Convert code <code>...</code> to `...`
    text = re.sub(r"<\s*code\s*>(.*?)<\s*/\s*code\s*>", r"`\1`", text, flags=re.IGNORECASE | re.DOTALL)
    # Remove any other remaining html tags
    text = re.sub(r"<[^>]+>", "", text)
    return text


class SlackNotifier(BaseNotifier):
    """Notifier for Slack using Incoming Webhooks."""

    def __init__(self, webhook_url: str | None, enabled: bool = True):
        self._webhook_url = webhook_url
        self._enabled = enabled and bool(webhook_url and webhook_url.startswith("https://hooks.slack.com"))

    @property
    def name(self) -> str:
        return "slack"

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    async def send_message(self, title: str, message: str, level: str = "info") -> bool:
        if not self.is_enabled or not self._webhook_url:
            return False

        icon = {
            "info": ":information_source:",
            "warning": ":warning:",
            "success": ":white_check_mark:",
            "error": ":rotating_light:",
        }.get(level, ":bell:")

        clean_title = re.sub(r"<[^>]+>", "", title)
        mrkdwn_message = html_to_mrkdwn(message)

        # Slack Block Kit payload for rich rendering
        payload = {
            "text": f"{icon} *{clean_title}*\n{mrkdwn_message}",
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": clean_title,
                        "emoji": True,
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": mrkdwn_message,
                    },
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": "🕒 *TiqueTaque Sync Personal Assistant*",
                        }
                    ],
                },
            ],
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(self._webhook_url, json=payload)
                if resp.status_code == 200:
                    logger.info("Slack notification sent: %s", title)
                    return True
                logger.error("Failed to send Slack notification: HTTP %s - %s", resp.status_code, resp.text)
                return False
        except Exception as e:
            logger.exception("Error sending Slack message: %s", e)
            return False
