"""Read/write the user configuration file edited by the wizard and the web UI."""

import json
import logging
import os
import tempfile
from typing import Any

from . import paths

logger = logging.getLogger(__name__)

#: Keys the UI is allowed to persist. Anything else in config.json is ignored on load
#: so a stale or hand-edited file can never inject unknown settings.
EDITABLE_KEYS = (
    "tiquetaque_email",
    "tiquetaque_code",
    "timezone",
    "work_hours_per_day",
    "lunch_duration_minutes",
    "lunch_warning_advance_minutes",
    "lunch_warning_final_minutes",
    "end_work_warning_advance_minutes",
    "end_work_warning_final_minutes",
    "continuous_work_limit_hours",
    "continuous_work_warning_advance_minutes",
    "continuous_work_warning_final_minutes",
    "poll_interval_seconds",
    "alert_ticker_interval_seconds",
    "telegram_enabled",
    "telegram_bot_token",
    "telegram_chat_id",
    "slack_enabled",
    "slack_webhook_url",
    "host",
    "port",
    "open_browser_on_start",
)

#: Never returned verbatim by the API; the UI only learns whether they are filled in.
SECRET_KEYS = ("tiquetaque_code", "telegram_bot_token", "slack_webhook_url")


def load() -> dict[str, Any]:
    """Load config.json, returning an empty dict when absent or corrupted."""
    path = paths.config_file()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Ignoring unreadable config file %s: %s", path, exc)
        return {}

    if not isinstance(raw, dict):
        logger.warning("Ignoring config file %s: expected a JSON object", path)
        return {}

    return {k: v for k, v in raw.items() if k in EDITABLE_KEYS}


def save(values: dict[str, Any]) -> dict[str, Any]:
    """Merge ``values`` into config.json and return the stored result."""
    current = load()
    current.update({k: v for k, v in values.items() if k in EDITABLE_KEYS})

    path = paths.config_file()
    path.parent.mkdir(parents=True, exist_ok=True)

    # Write through a temp file so an interrupted save never truncates the config.
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".config-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(current, fh, indent=2, ensure_ascii=False, sort_keys=True)
        os.replace(tmp_name, path)
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise

    _restrict_permissions(path)
    logger.info("Saved configuration to %s", path)
    return current


def _restrict_permissions(path) -> None:
    """Best-effort 0600 on POSIX — the file holds the TiqueTaque code and bot tokens."""
    if os.name == "posix":
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def mask(values: dict[str, Any]) -> dict[str, Any]:
    """Replace secrets with a boolean ``<key>_is_set`` flag for safe API exposure."""
    safe = {k: v for k, v in values.items() if k not in SECRET_KEYS}
    for key in SECRET_KEYS:
        safe[f"{key}_is_set"] = bool(values.get(key))
    return safe
