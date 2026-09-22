"""Gera packaging/icon.ico — o relógio roxo usado pelo executável do Windows.

Só biblioteca padrão: desenha um RGBA de 256x256 na mão, comprime como PNG com
zlib e embrulha no cabeçalho ICO (o formato aceita um PNG inteiro desde o Vista).

O .ico é versionado, então isto só precisa rodar quando o desenho mudar:

    python packaging/make_icon.py
"""

import math
import struct
import zlib
from pathlib import Path

SIZE = 256
OUT = Path(__file__).resolve().parent / "icon.ico"

BRAND = (139, 92, 246, 255)      # roxo da identidade (#8b5cf6)
FACE = (18, 20, 30, 255)         # fundo escuro do painel (#12141e)
HANDS = (243, 244, 246, 255)     # ponteiros (#f3f4f6)
EMPTY = (0, 0, 0, 0)


def _blend(base, over, alpha):
    """Mistura `over` sobre `base` com cobertura 0..1 (antialiasing)."""
    if alpha <= 0:
        return base
    if alpha >= 1 and over[3] == 255:
        return over
    out = []
    for i in range(3):
        out.append(round(base[i] * (1 - alpha) + over[i] * alpha))
    out.append(max(base[3], round(over[3] * alpha)))
    return tuple(out)


def _coverage(distance: float, edge: float) -> float:
    """Antialiasing simples: 1 dentro, 0 fora, rampa de 1px na borda."""
    return max(0.0, min(1.0, edge + 0.5 - distance))


def _draw_hand(pixels, angle_deg: float, length: float, width: float) -> None:
    """Desenha um ponteiro do centro do relógio no ângulo dado (0° = 12h)."""
    cx = cy = SIZE / 2
    angle = math.radians(angle_deg - 90)
    ex, ey = cx + math.cos(angle) * length, cy + math.sin(angle) * length

    dx, dy = ex - cx, ey - cy
    seg_len_sq = dx * dx + dy * dy

    for y in range(SIZE):
        for x in range(SIZE):
            px, py = x + 0.5 - cx, y + 0.5 - cy
            # Projeção do ponto no segmento (t recortado em 0..1)
            t = max(0.0, min(1.0, (px * dx + py * dy) / seg_len_sq))
            dist = math.hypot(px - dx * t, py - dy * t)
            cov = _coverage(dist, width / 2)
            if cov > 0:
                pixels[y][x] = _blend(pixels[y][x], HANDS, cov)


def build_pixels() -> list[list[tuple]]:
    cx = cy = SIZE / 2
    outer = SIZE / 2 - 8         # borda externa do anel
    ring = 18                    # espessura do anel roxo

    pixels = [[EMPTY] * SIZE for _ in range(SIZE)]

    for y in range(SIZE):
        for x in range(SIZE):
            dist = math.hypot(x + 0.5 - cx, y + 0.5 - cy)
            face_cov = _coverage(dist, outer - ring)
            ring_cov = _coverage(dist, outer)

            color = EMPTY
            if ring_cov > 0:
                color = _blend(color, BRAND, ring_cov)
            if face_cov > 0:
                color = _blend(color, FACE, face_cov)
            pixels[y][x] = color

    # 10:10 — a pose clássica de vitrine de relojoaria
    _draw_hand(pixels, 300, outer - ring - 26, 14)   # horas
    _draw_hand(pixels, 60, outer - ring - 8, 10)     # minutos
    return pixels


def encode_png(pixels) -> bytes:
    raw = bytearray()
    for row in pixels:
        raw.append(0)  # filtro "None" por linha
        for pixel in row:
            raw.extend(pixel)

    def chunk(tag: bytes, payload: bytes) -> bytes:
        body = tag + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0)  # 8 bits, RGBA
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def encode_ico(png: bytes) -> bytes:
    # ICONDIR: reservado, tipo 1 (ícone), 1 imagem
    header = struct.pack("<HHH", 0, 1, 1)
    # ICONDIRENTRY: 0 em largura/altura significa 256
    entry = struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png), 6 + 16)
    return header + entry + png


def main() -> None:
    OUT.write_bytes(encode_ico(encode_png(build_pixels())))
    print(f"Ícone gerado: {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
