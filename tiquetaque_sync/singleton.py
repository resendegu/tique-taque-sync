"""Trava de instância única, baseada em lock de arquivo do sistema operacional.

Duas travas independentes são usadas pelo app:

* ``service`` — impede dois servidores sincronizando a mesma jornada, o que
  duplicaria notificações e geraria disputa pelo mesmo SQLite.
* ``gui``     — impede duas janelas de controle abertas ao mesmo tempo.

O lock é do SO (``flock`` no POSIX, ``msvcrt.locking`` no Windows), e não um
arquivo-sentinela: o sistema o libera sozinho quando o processo morre, então um
desligamento abrupto não deixa trava fantasma para trás.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .config import settings


class SingleInstance:
    """Trava nomeada. ``acquire()`` devolve False quando já há outra instância."""

    def __init__(self, name: str, directory: Path | None = None):
        self.name = name
        base = directory or settings.data_dir
        self.path = Path(base) / f"{name}.lock"
        self._handle = None

    def acquire(self) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Sem truncar: o conteúdo é só informativo e o lock é do SO.
            handle = open(self.path, "a+", encoding="utf-8")
        except OSError:
            # Sem permissão para criar o arquivo: melhor deixar rodar do que
            # travar o app por causa da trava.
            return True

        if not _lock(handle):
            handle.close()
            return False

        self._handle = handle
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(str(os.getpid()))
            handle.flush()
        except OSError:
            pass
        return True

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            _unlock(self._handle)
        finally:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> "SingleInstance":
        self.acquired = self.acquire()
        return self

    def __exit__(self, *exc_info) -> None:
        self.release()


def _lock(handle) -> bool:
    try:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(handle) -> None:
    try:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def focus_existing_window(title: str) -> bool:
    """Traz a janela de outra instância para frente (só Windows).

    Usado quando o usuário abre o app uma segunda vez: em vez de não acontecer
    nada, a janela que já existe aparece.
    """
    if sys.platform != "win32":
        return False

    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, title)
        if not hwnd:
            return False

        SW_RESTORE = 9
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        else:
            user32.ShowWindow(hwnd, 5)  # SW_SHOW
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False
