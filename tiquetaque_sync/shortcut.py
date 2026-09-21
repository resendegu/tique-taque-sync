"""Cria um atalho para a janela do app, para não depender do terminal.

* Windows — ``.lnk`` no Menu Iniciar e na Área de Trabalho (via WScript.Shell).
* Linux   — ``.desktop`` em ``~/.local/share/applications``.
* macOS   — ``.command`` executável em ``~/Applications``.

Tudo é escrito no perfil do usuário e nada exige privilégio elevado.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .autostart import DISPLAY_NAME, launch_workdir

UNIT_NAME = "tiquetaque-sync"


class ShortcutError(RuntimeError):
    """Raised when the shortcut cannot be created or removed."""


def _gui_command() -> list[str]:
    """Comando que abre a janela sem console."""
    script = shutil.which("tiquetaque-sync-gui")
    if script:
        return [script]

    python = sys.executable
    if sys.platform == "win32":
        pythonw = Path(python).with_name("pythonw.exe")
        if pythonw.exists():
            python = str(pythonw)
    return [python, "-m", "tiquetaque_sync", "gui"]


# ------------------------------------------------------------------------------
# Windows
# ------------------------------------------------------------------------------
def _windows_targets() -> list[Path]:
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    start_menu = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    desktop = Path(os.environ.get("USERPROFILE") or Path.home()) / "Desktop"
    return [start_menu / f"{DISPLAY_NAME}.lnk", desktop / f"{DISPLAY_NAME}.lnk"]


def _windows_create() -> list[Path]:
    command = _gui_command()
    target = command[0]
    arguments = " ".join(f'"{a}"' if " " in a else a for a in command[1:])
    workdir = launch_workdir() or str(Path(target).parent)

    created: list[Path] = []
    for link in _windows_targets():
        link.parent.mkdir(parents=True, exist_ok=True)
        script = (
            'Set sh = CreateObject("WScript.Shell")\r\n'
            f'Set lnk = sh.CreateShortcut("{link}")\r\n'
            f'lnk.TargetPath = "{target}"\r\n'
            f'lnk.Arguments = "{arguments}"\r\n'
            f'lnk.WorkingDirectory = "{workdir}"\r\n'
            f'lnk.Description = "{DISPLAY_NAME} — monitor de jornada"\r\n'
            "lnk.Save\r\n"
        )
        _run_vbs(script)
        created.append(link)
    return created


def _run_vbs(script: str) -> None:
    fd, name = tempfile.mkstemp(suffix=".vbs")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(script)
        result = subprocess.run(
            ["cscript", "//Nologo", name], capture_output=True, text=True
        )
        if result.returncode != 0:
            raise ShortcutError(result.stderr.strip() or "cscript falhou ao criar o atalho")
    finally:
        os.unlink(name)


# ------------------------------------------------------------------------------
# Linux / macOS
# ------------------------------------------------------------------------------
def _linux_target() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "applications" / f"{UNIT_NAME}.desktop"


def _linux_create() -> list[Path]:
    entry = _linux_target()
    entry.parent.mkdir(parents=True, exist_ok=True)
    command = " ".join(f'"{p}"' if " " in p else p for p in _gui_command())
    workdir = launch_workdir()
    entry.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={DISPLAY_NAME}\n"
        "Comment=Monitor de jornada e notificações\n"
        f"Exec={command}\n"
        + (f"Path={workdir}\n" if workdir else "")
        + "Terminal=false\n"
        "Categories=Utility;Office;\n",
        encoding="utf-8",
    )
    entry.chmod(0o755)
    return [entry]


def _macos_target() -> Path:
    return Path.home() / "Applications" / f"{DISPLAY_NAME}.command"


def _macos_create() -> list[Path]:
    entry = _macos_target()
    entry.parent.mkdir(parents=True, exist_ok=True)
    command = " ".join(f'"{p}"' if " " in p else p for p in _gui_command())
    workdir = launch_workdir()
    entry.write_text(
        "#!/bin/sh\n"
        + (f'cd "{workdir}"\n' if workdir else "")
        + f"exec {command}\n",
        encoding="utf-8",
    )
    entry.chmod(0o755)
    return [entry]


# ------------------------------------------------------------------------------
# API pública
# ------------------------------------------------------------------------------
def targets() -> list[Path]:
    if sys.platform == "win32":
        return _windows_targets()
    if sys.platform == "darwin":
        return [_macos_target()]
    return [_linux_target()]


def create() -> list[Path]:
    if sys.platform == "win32":
        return _windows_create()
    if sys.platform == "darwin":
        return _macos_create()
    if sys.platform.startswith("linux"):
        return _linux_create()
    raise ShortcutError(f"Atalho não suportado em {sys.platform}.")


def remove() -> list[Path]:
    removed = []
    for path in targets():
        if path.exists():
            path.unlink()
            removed.append(path)
    return removed


def exists() -> bool:
    return any(path.exists() for path in targets())
