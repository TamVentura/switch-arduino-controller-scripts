"""
research-level-10/read_regions.py — que Pokémon aparecem em cada região.

Só os nomes. As research tasks são iguais em todo o lado: a região é apenas um
filtro sobre a lista, por isso não há nada a reler no painel.

Precisa do `free-play/main.py` a correr, e reutiliza a leitura da lista do
`read_checks.py` (linha selecionada pelo pixel creme, nome por OCR com fuzzy
match contra a dex).

    python research-level-10/read_regions.py

Estado de partida: **Entire Hisui Region** e o cursor no primeiro mon. Como
cada região guarda o seu próprio cursor, e cada uma é percorrida de ponta a
ponta (a lista dá wrap), no fim de cada região o cursor volta ao primeiro —
o estado fica igual ao inicial e o script pode correr outra vez.
"""

from __future__ import annotations

import json
import os
import sys
import time

import cv2
import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import read_checks as rc  # noqa: E402

import capture  # noqa: E402
import tesserocr  # noqa: E402

OUT_JSON = os.path.join(_HERE, "regions.json")

# Ordem em que o ZR roda as posições do filtro, medida ao vivo. A primeira não
# é uma região: é a lista toda, e o Tiago não a quer.
REGIONS = [
    "Entire Hisui Region",
    "Obsidian Fieldlands",
    "Crimson Mirelands",
    "Cobalt Coastlands",
    "Coronet Highlands",
    "Alabaster Icelands",
]

REGION_NAME_BOX = (2750, 336, 3810, 420)
# Primeiro contador do topo: quantos mons tem a lista com este filtro. Lê-se
# com Tesseract, que num número horizontal de 2-3 dígitos é fiável (ao
# contrário dos dígitos isolados da coluna dos valores — ver read_checks).
COUNT_BOX = (1650, 80, 1830, 190)

WAKE_EVERY = 20  # o d-pad já mantém o ecrã aceso; isto é só para as pausas


def read_region_name() -> tuple[str | None, str]:
    """A região activa, encostada por fuzzy match aos 6 nomes conhecidos."""
    im = rc.shot()
    crop = capture.get_frame_region(*REGION_NAME_BOX, frame=im)
    raw = rc._ocr(crop)
    best, score = None, 0.0
    for name in REGIONS:
        import difflib

        s = difflib.SequenceMatcher(None, raw.lower(), name.lower()).ratio()
        if s > score:
            best, score = name, s
    return (best if score >= 0.7 else None), raw


def read_count(im: np.ndarray) -> int | None:
    """Quantos mons a lista tem com o filtro actual."""
    crop = capture.get_frame_region(*COUNT_BOX, frame=im)
    b, g, r = (crop[:, :, k].astype(int) for k in range(3))
    dark = ((r < 140) & (g < 140) & (b < 160)).astype(np.uint8)
    n, _, stats, _ = cv2.connectedComponentsWithStats(dark, 8)
    glyphs = [stats[i] for i in range(1, n) if stats[i][4] >= 40]
    if not glyphs:
        return None
    pad = 6
    x1 = max(0, min(q[0] for q in glyphs) - pad)
    y1 = max(0, min(q[1] for q in glyphs) - pad)
    x2 = max(q[0] + q[2] for q in glyphs) + pad
    y2 = max(q[1] + q[3] for q in glyphs) + pad
    txt = rc._ocr(crop[y1:y2, x1:x2], "0123456789", tesserocr.PSM.SINGLE_WORD)
    # o nº de dígitos lidos tem de bater com o nº de glifos encontrados
    if not txt.isdigit() or len(txt) != len(glyphs):
        return None
    return int(txt)


def walk_region(expected: int | None) -> tuple[list[str], list[str]]:
    """Percorre a lista de ponta a ponta, um mon de cada vez.

    Devolve os nomes e os problemas. Pára quando volta ao primeiro nome (a
    lista dá wrap), o que também confirma que não se perdeu nenhum passo.
    """
    names: list[str] = []
    problems: list[str] = []
    limit = (expected + 5) if expected else 260

    for i in range(limit):
        im = rc.shot() if i % WAKE_EVERY == 0 else rc.shot_only()
        lst = rc.read_list(im)
        name, score = rc.match_name(lst["name_ocr"])
        if name is None or score < 0.55:
            problems.append(f"#{i + 1}: nome incerto {lst['name_ocr']!r} ({score})")
            name = f"?{lst['name_ocr']}"
        if names and name == names[0]:
            break  # deu a volta
        if name in names:
            problems.append(f"#{i + 1}: {name} repetido sem ter dado a volta")
        names.append(name)
        rc.step_down()
        time.sleep(0.28)
    else:
        problems.append("não deu a volta dentro do limite")

    if expected is not None and len(names) != expected:
        problems.append(f"li {len(names)} nomes, o contador diz {expected}")
    return names, problems


def main() -> None:
    where, raw = read_region_name()
    print(f"região actual: {where} ({raw!r})")
    if where is None:
        raise SystemExit("não consegui ler o nome da região")

    out: dict[str, list[str]] = {}
    all_problems: dict[str, list[str]] = {}

    # Avança com ZR até passar pelas 5 regiões a seguir à "Entire Hisui Region".
    for _ in range(len(REGIONS)):
        # toque curto, como no d-pad: o /press segura 0.2s e pode saltar duas
        rc._get("/hold?key=r&secs=0.05")
        time.sleep(0.7)
        where, raw = read_region_name()
        if where is None:
            raise SystemExit(f"não consegui ler o nome da região ({raw!r})")
        if where == REGIONS[0]:
            break  # deu a volta ao filtro

        im = rc.shot()
        count = read_count(im)
        print(f"\n{where} — o contador diz {count}")

        names, problems = walk_region(count)
        out[where] = names
        if problems:
            all_problems[where] = problems
        print(f"  {len(names)} mons: {', '.join(names)}")
        for p in problems:
            print(f"  !! {p}")

        with open(OUT_JSON, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=1)

    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print(f"\n{len(out)} regiões -> {OUT_JSON}")
    for region, problems in all_problems.items():
        print(f"  {region}: {len(problems)} avisos")


if __name__ == "__main__":
    main()
