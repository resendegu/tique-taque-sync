"""Atualização automática a partir das releases do GitHub.

Só o executável é trocado. Configuração (`config.json`) e banco de dados moram
em diretórios do usuário, fora do `.exe`, então atualizar **não** pede
reconfiguração — credenciais, canais e horários continuam onde estavam.

Segurança, porque aqui se baixa e se executa um binário:

* O repositório é fixo no código (:data:`REPO`), não configurável por arquivo
  nem por variável de ambiente. Não há como apontar o updater para outro lugar.
* Só HTTPS, e só ativos da própria release.
* O `.exe` baixado é conferido contra o `.sha256` publicado na mesma release;
  divergiu, o arquivo é descartado e nada é instalado.
* A troca em si nunca acontece sozinha no meio do uso: o download fica
  *staged* e só é aplicado no próximo início do app.

Instalações via `pip` não são trocadas por aqui — para elas o updater apenas
avisa que há versão nova e sugere `pip install --upgrade`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .autostart import child_environment, is_frozen
from .config import settings

logger = logging.getLogger(__name__)

REPO = "resendegu/tique-taque-sync"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"

ASSET_NAME = "TiqueTaqueSync.exe"
CHECKSUM_SUFFIX = ".sha256"

USER_AGENT = f"TiqueTaqueSync/{__version__} (+https://github.com/{REPO})"
TIMEOUT = 15


@dataclass
class Release:
    version: str
    tag: str
    download_url: str | None
    checksum_url: str | None
    page_url: str

    @property
    def has_executable(self) -> bool:
        return bool(self.download_url and self.checksum_url)


# ------------------------------------------------------------------ versões
def parse_version(version: str) -> tuple[int, int, int]:
    """'2.1.0' ou 'v2.1.0-rc.1' -> (2, 1, 0). Inválido vira (0, 0, 0)."""
    core = version.strip().lstrip("vV").split("-", 1)[0]
    parts: list[int] = []
    for piece in core.split(".")[:3]:
        try:
            parts.append(int(piece))
        except ValueError:
            return (0, 0, 0)
    while len(parts) < 3:
        parts.append(0)
    return (parts[0], parts[1], parts[2])


def is_newer(candidate: str, current: str = __version__) -> bool:
    return parse_version(candidate) > parse_version(current)


# ------------------------------------------------------------------- consulta
def _open(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    if not url.lower().startswith("https://"):
        raise ValueError(f"Recusando URL sem HTTPS: {url}")
    return urllib.request.urlopen(request, timeout=TIMEOUT, context=ssl.create_default_context())


def fetch_latest() -> Release | None:
    """Consulta a última release. None quando a rede falha ou não há release."""
    try:
        with _open(RELEASES_API) as response:
            payload = json.loads(response.read())
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        logger.info("Não foi possível consultar atualizações: %s", exc)
        return None

    tag = str(payload.get("tag_name") or "")
    if not tag:
        return None

    download_url = checksum_url = None
    for asset in payload.get("assets") or []:
        name = asset.get("name") or ""
        url = asset.get("browser_download_url") or ""
        if name == ASSET_NAME:
            download_url = url
        elif name == ASSET_NAME + CHECKSUM_SUFFIX:
            checksum_url = url

    return Release(
        version=tag.lstrip("vV"),
        tag=tag,
        download_url=download_url,
        checksum_url=checksum_url,
        page_url=payload.get("html_url") or RELEASES_PAGE,
    )


def check_for_update() -> Release | None:
    """Devolve a release apenas quando ela é mais nova que a instalada."""
    release = fetch_latest()
    if release and is_newer(release.version):
        return release
    return None


# ------------------------------------------------------------------- download
def _updates_dir() -> Path:
    path = Path(settings.data_dir) / "updates"
    path.mkdir(parents=True, exist_ok=True)
    return path


def pending_path() -> Path:
    return _updates_dir() / "pending.exe"


def pending_marker() -> Path:
    return _updates_dir() / "pending.json"


def _expected_checksum(url: str) -> str | None:
    """Lê o `.sha256` publicado. Formato: '<hash>  TiqueTaqueSync.exe'."""
    try:
        with _open(url) as response:
            text = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        logger.warning("Não foi possível baixar o checksum: %s", exc)
        return None

    token = text.strip().split()[0] if text.strip() else ""
    return token.lower() if len(token) == 64 else None


def download(release: Release) -> Path | None:
    """Baixa e confere o executável. Devolve o caminho staged, ou None."""
    if not release.has_executable:
        logger.warning("Release %s não traz %s — nada a baixar.", release.tag, ASSET_NAME)
        return None

    expected = _expected_checksum(release.checksum_url)
    if not expected:
        logger.warning("Checksum indisponível: atualização abortada por segurança.")
        return None

    temp = _updates_dir() / "download.part"
    digest = hashlib.sha256()
    try:
        with _open(release.download_url) as response, open(temp, "wb") as handle:
            while chunk := response.read(64 * 1024):
                digest.update(chunk)
                handle.write(chunk)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        logger.warning("Falha ao baixar a atualização: %s", exc)
        temp.unlink(missing_ok=True)
        return None

    if digest.hexdigest().lower() != expected:
        logger.error(
            "Checksum não confere para %s — arquivo descartado (esperado %s, obtido %s).",
            release.tag, expected, digest.hexdigest().lower(),
        )
        temp.unlink(missing_ok=True)
        return None

    target = pending_path()
    target.unlink(missing_ok=True)
    os.replace(temp, target)

    pending_marker().write_text(
        json.dumps({"version": release.version, "tag": release.tag}, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("Atualização %s baixada e verificada.", release.tag)
    return target


def pending_version() -> str | None:
    """Versão já baixada e à espera de reinício, se houver."""
    if not pending_path().exists():
        return None
    try:
        return json.loads(pending_marker().read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError):
        return None


def discard_pending() -> None:
    pending_path().unlink(missing_ok=True)
    pending_marker().unlink(missing_ok=True)


# -------------------------------------------------------------------- aplicar
def _backup_path(executable: Path) -> Path:
    return executable.with_name(executable.stem + ".old" + executable.suffix)


def cleanup_backup() -> None:
    """Remove o executável antigo deixado pela troca anterior."""
    if not is_frozen():
        return
    backup = _backup_path(Path(sys.executable))
    try:
        backup.unlink(missing_ok=True)
    except OSError:
        # Ainda travado pelo processo que acabou de sair; a próxima execução limpa.
        pass


def apply_pending_update(relaunch: bool = True) -> bool:
    """Troca o executável pela versão baixada. True se trocou (e relançou).

    Chamado no **início** do processo, antes de qualquer trava ou servidor: é
    quando não há nada em uso para atrapalhar. O Windows não deixa sobrescrever
    um `.exe` em execução, mas deixa renomeá-lo — é disso que a troca depende.
    """
    if not is_frozen():
        return False

    staged = pending_path()
    if not staged.exists():
        return False

    target = Path(sys.executable)
    backup = _backup_path(target)

    try:
        backup.unlink(missing_ok=True)
        os.replace(target, backup)
    except OSError as exc:
        logger.warning("Não foi possível liberar o executável atual: %s", exc)
        return False

    try:
        shutil.move(str(staged), str(target))
    except OSError as exc:
        logger.error("Falha ao instalar a atualização, revertendo: %s", exc)
        try:
            os.replace(backup, target)
        except OSError:
            logger.critical(
                "O executável ficou em %s — renomeie de volta para %s manualmente.",
                backup, target,
            )
        return False

    pending_marker().unlink(missing_ok=True)
    logger.info("Atualização instalada em %s", target)

    if relaunch:
        try:
            kwargs = {"env": child_environment()}
            if sys.platform == "win32":
                kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
            subprocess.Popen([str(target), *sys.argv[1:]], **kwargs)
        except OSError as exc:
            logger.error("Atualizado, mas não consegui reabrir o app: %s", exc)
    return True


def update_hint() -> str:
    """Como atualizar quando não é o executável (instalação via pip)."""
    return f"pip install --upgrade git+https://github.com/{REPO}.git"
