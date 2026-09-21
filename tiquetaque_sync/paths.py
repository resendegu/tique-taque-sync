"""Per-user config and data directory resolution (Windows, macOS, Linux)."""

import os
import sys
from pathlib import Path

APP_NAME = "TiqueTaqueSync"
APP_SLUG = "tiquetaque-sync"

#: Overrides every other location. Useful for tests, portable installs and Docker.
HOME_ENV_VAR = "TIQUETAQUE_SYNC_HOME"


def _portable_home() -> Path | None:
    override = os.environ.get(HOME_ENV_VAR)
    return Path(override).expanduser() if override else None


def config_dir() -> Path:
    """Directory holding ``config.json`` written by the setup wizard and the web UI."""
    portable = _portable_home()
    if portable:
        return portable

    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming"
        return Path(base) / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / APP_SLUG


def data_dir() -> Path:
    """Directory holding the SQLite database and logs."""
    portable = _portable_home()
    if portable:
        return portable / "data"

    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
        return Path(base) / APP_NAME / "data"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME / "data"
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / APP_SLUG


def config_file() -> Path:
    return config_dir() / "config.json"


def ensure_config_dir() -> Path:
    """Cria (se preciso) o diretório de configuração e o devolve.

    O diretório de dados não é criado aqui de propósito: ele pode ser
    redirecionado por ``DATA_DIR`` e quem o cria é a ``Database``, a partir do
    valor efetivo de ``settings.data_dir``.
    """
    path = config_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path
