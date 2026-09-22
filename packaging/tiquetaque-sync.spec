# -*- mode: python ; coding: utf-8 -*-
"""Receita do executável Windows (TiqueTaqueSync.exe).

    pip install pyinstaller
    pyinstaller packaging/tiquetaque-sync.spec --noconfirm

Gera `dist/TiqueTaqueSync.exe`: arquivo único, sem console, que abre a janela
quando executado sem argumentos e age como CLI quando recebe argumentos.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

REPO_ROOT = Path(SPECPATH).resolve().parent
PACKAGE = REPO_ROOT / "tiquetaque_sync"

# Templates, CSS, JS e ícone do painel: sem isto o .exe sobe mas não renderiza.
datas = [
    (str(PACKAGE / "web"), "tiquetaque_sync/web"),
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

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="TiqueTaqueSync",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX dispara falso-positivo em vários antivírus
    runtime_tmpdir=None,
    console=False,  # janela do app, não terminal
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(Path(SPECPATH) / "icon.ico"),
)
