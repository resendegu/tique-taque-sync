# -*- mode: python ; coding: utf-8 -*-
"""Receita do executável Windows (TiqueTaqueSync.exe).

    pip install pyinstaller
    pyinstaller packaging/tiquetaque-sync.spec --noconfirm              # arquivo único
    TTQ_ONEDIR=1 pyinstaller packaging/tiquetaque-sync.spec --noconfirm # pasta

Os dois modos produzem o mesmo app — sem console, abrindo a janela quando
executado sem argumentos e agindo como CLI quando recebe argumentos.

Por que dois modos: o `--onefile` se descompacta em `%TEMP%` a cada execução, e
esse comportamento é um dos gatilhos clássicos de heurística de antivírus. O
modo pasta não faz isso e é bem menos sinalizado — em troca, é um diretório
inteiro em vez de um arquivo só. Publicamos os dois e deixamos o usuário
escolher.
"""

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

REPO_ROOT = Path(SPECPATH).resolve().parent
PACKAGE = REPO_ROOT / "tiquetaque_sync"

ONEDIR = os.environ.get("TTQ_ONEDIR", "").lower() in ("1", "true", "yes")

# Recurso VERSIONINFO gerado a partir da versão do pyproject.toml. Executável
# sem esses campos pontua mal nos motores de antivírus baseados em ML.
sys.path.insert(0, str(Path(SPECPATH)))
import version_info as version_info_module  # noqa: E402

VERSION_FILE = version_info_module.write(
    REPO_ROOT, Path(SPECPATH) / "version_info.txt"
)

# Templates, CSS, JS e ícone do painel: sem isto o .exe sobe mas não renderiza.
datas = [
    (str(PACKAGE / "web"), "tiquetaque_sync/web"),
    # Ícone do app: a janela Tk e a bandeja o carregam em tempo de execução,
    # então não basta o `icon=` do EXE (que é só o recurso do arquivo).
    (str(PACKAGE / "assets"), "tiquetaque_sync/assets"),
]

# uvicorn e apscheduler carregam módulos por nome em tempo de execução, então o
# analisador estático do PyInstaller não os enxerga.
hiddenimports = (
    collect_submodules("uvicorn")
    + collect_submodules("apscheduler")
    + [
        "anyio._backends._asyncio",
        "pydantic_settings",
        "tiquetaque_sync.main",
    ]
)

a = Analysis(
    [str(Path(SPECPATH) / "entry.py")],
    pathex=[str(REPO_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "unittest", "pip", "setuptools"],
    noarchive=False,
)

pyz = PYZ(a.pure)

common = dict(
    name="TiqueTaqueSync",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX é um dos sinais mais fortes de malware para antivírus
    console=False,  # janela do app, não terminal
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(Path(SPECPATH) / "icon.ico"),
    version=str(VERSION_FILE),
)

if ONEDIR:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, **common)
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        name="TiqueTaqueSync",
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        runtime_tmpdir=None,
        **common,
    )
