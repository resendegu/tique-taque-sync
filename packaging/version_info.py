"""Gera o recurso VERSIONINFO embutido no TiqueTaqueSync.exe.

Um executável sem esse recurso não tem nome de produto, empresa, versão nem
descrição — e é exatamente assim que a maioria dos droppers de malware sai do
compilador. Motores de antivírus baseados em aprendizado de máquina usam essa
ausência como sinal, então preencher os campos reduz falso-positivo (não
elimina: só assinatura digital resolve de fato).

O conteúdo é derivado do `__version__` do pacote (a fonte única de versão).
"""

from __future__ import annotations

import re
from pathlib import Path

COMPANY = "Gustavo Resende"
PRODUCT = "TiqueTaque Sync"
DESCRIPTION = "Monitor de jornada de trabalho com painel local e notificações"
COPYRIGHT = "MIT License — github.com/resendegu/tique-taque-sync"
ORIGINAL_FILENAME = "TiqueTaqueSync.exe"

TEMPLATE = """# Gerado por packaging/version_info.py — não edite à mão.
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={tup},
    prodvers={tup},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
      StringTable(
        '040904B0',
        [StringStruct('CompanyName', {company!r}),
        StringStruct('FileDescription', {description!r}),
        StringStruct('FileVersion', {version!r}),
        StringStruct('InternalName', {product!r}),
        StringStruct('LegalCopyright', {copyright!r}),
        StringStruct('OriginalFilename', {filename!r}),
        StringStruct('ProductName', {product!r}),
        StringStruct('ProductVersion', {version!r})])
      ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


VERSION_ATTR = re.compile(r"""^__version__\s*=\s*["'](?P<version>[^"']+)["']""", re.M)


def project_version(repo_root: Path) -> str:
    """Lê a versão da fonte única: `__version__` do pacote.

    O `pyproject.toml` a deriva via `dynamic`, então não há versão estática lá
    para ler — ler de lá quebrava o build com `KeyError: 'version'`.
    """
    init = repo_root / "tiquetaque_sync" / "__init__.py"
    match = VERSION_ATTR.search(init.read_text(encoding="utf-8"))
    if match is None:
        raise SystemExit(f"{init} não define __version__")
    return match["version"]


def version_tuple(version: str) -> tuple[int, int, int, int]:
    """'2.1.0' ou '2.1.0-rc.1' -> (2, 1, 0, 0), que é o formato do Windows."""
    core = version.split("-", 1)[0]
    parts = [int(piece) for piece in core.split(".")][:3]
    while len(parts) < 3:
        parts.append(0)
    return (parts[0], parts[1], parts[2], 0)


def render(version: str) -> str:
    return TEMPLATE.format(
        tup=version_tuple(version),
        company=COMPANY,
        description=DESCRIPTION,
        version=version,
        product=PRODUCT,
        copyright=COPYRIGHT,
        filename=ORIGINAL_FILENAME,
    )


def write(repo_root: Path, destination: Path) -> Path:
    version = project_version(repo_root)
    destination.write_text(render(version), encoding="utf-8")
    return destination


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    out = write(root, root / "packaging" / "version_info.txt")
    print(f"Gerado: {out}")
