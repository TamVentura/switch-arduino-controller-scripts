"""
research-level-10/read_checks.py — lê do ecrã, mon a mon, quantos passos
(vistos vermelhos) estão feitos em cada research task.

Só isso sai do ecrã. Os nomes das tarefas, os patamares e as flags de bónus
vivem em C:\\Projects\\pla-dex-data (raspados do game8) e não se leem aqui.

Precisa do `free-play/main.py` a correr (é o único processo que pode ter o
Elgato aberto); fala com ele pelo canal HTTP em 127.0.0.1:8777.

    python research-level-10/read_checks.py             # anda até aos completos
    python research-level-10/read_checks.py --max 5     # só os primeiros 5
    python research-level-10/read_checks.py --shot x.png  # lê um png e sai

A lista tem de estar em **Entire Hisui Region**, na **aba 2 (Research Tasks)**,
**ordenada por research level** (o `Y` alterna) e com o cursor **no topo**.
Assim os incompletos vêm todos primeiro e basta descer.

Como se lê o painel
-------------------
O painel está ligeiramente torto: os vistos caem mais à direita a cada linha
que se desce e mais acima a cada coluna que se avança. Por isso nada aqui usa
uma grelha fixa — as linhas e as colunas saem sempre dos próprios pixels dos
pills, e só depois se pergunta, célula a célula, se lá está o vermelho do
visto.

Os números não passam pelo Tesseract: glifo a glifo ele devolve vazio num `0`
isolado, e a coluna toda de uma vez funde linhas iguais (quatro `1` seguidos
vinham como um só). Como a fonte é fixa, comparam-se os glifos com templates
(`digits.json`). Um glifo que não case com nada é guardado em `unknown_digits/`
em PNG e .npy, para se rotular e recalcular o valor sem voltar ao jogo.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import sys
import time
import urllib.request

import cv2
import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "utils"))

import capture  # noqa: E402  (só os helpers de cor; quem abre o Elgato é o free-play)
import settings  # noqa: E402
import tesserocr  # noqa: E402
from PIL import Image  # noqa: E402

SERVER = "http://127.0.0.1:8777"
SHOT_PNG = os.path.join(_HERE, "..", "free-play", "shots", "latest.png")
OUT_JSON = os.path.join(_HERE, "checks.json")
OUT_KEYS = (
    "mon", "name_ocr", "name_score", "research_level", "complete",
    "checks", "values",
)
FAIL_DIR = os.path.join(_HERE, "failed")
DEX_INDEX = r"C:\Projects\pla-dex-data\data\index.json"

# ── Geometria, em coordenadas 4K como no resto do projeto ────────────────────
PANEL = (90, 218, 2740, 1900)  # painel das research tasks
LIST = (2814, 444, 3722, 1870)  # lista de Pokémon (5 entradas visíveis)
LEVEL_BOX = (2450, 1680, 2730, 1840)  # a caixa do Research Level

# Dentro do painel (coordenadas locais do recorte, que sai a 1325x841):
PILLS_X = (800, 1290)  # a faixa onde vivem os pills
ROW_H = (25, 50)  # altura plausível de uma banda de pills
PILL_W_MIN = 45  # largura mínima de um pill
# Uma linha com um pill só (ex.: "Number you've caught while they were
# sleeping") dá ~74 px de pill por linha de scan. O limiar tem de ficar abaixo
# disso ou essas linhas desaparecem sem dar erro.
BAND_MIN_PX = 40

# Na lista: um pixel por linha, na parte de cima do cartão, à direita do
# sprite e acima do badge. A linha selecionada é creme; as outras escuras.
LIST_ROW_PX = [(3400, 540 + 280 * k) for k in range(5)]
LIST_PX_TO_CENTER = 60  # do pixel testado até ao centro da linha
SELECTED_HEX = "#f4e5d1"
SELECTED_TOL = 20

# Nome da linha selecionada, relativo ao centro da linha (4K)
NAME_BOX = (3100, -52, 3540, 52)  # x1, dy1, x2, dy2

# Coluna da pokébola: a mesma para as 5 linhas, cortada por linha. Um mon com
# pokébola está a research level 10 — é onde se pára.
BADGE_BOX = (3508, -110, 3646, 110)
BADGE_HEX = "#e8443f"
BADGE_TOL = 45
BADGE_MIN_RATIO = 0.02  # com pokébola dá ~0.06, sem dá 0.0000

# Coluna do valor actual de cada tarefa, à esquerda dos pills. Não se lê com
# Tesseract: glifo a glifo ele devolve vazio num `0` isolado, e a coluna toda
# de uma vez funde linhas iguais (quatro `1` seguidos vinham como um só). Como
# a fonte é fixa e os glifos são pixel-idênticos entre ocorrências (variam no
# máximo 5% dos pixels, contra 23% de distância entre dígitos diferentes),
# usam-se templates — determinístico e com margem de sobra.
VALUE_X = (1488, 1720)
VALUE_PAD = 10       # folga em y para o glifo caber na banda da linha
DIGIT_AREA_MIN = 40  # um dígito medido dá 73-157; os pontinhos ficam abaixo
DIGIT_H = (14, 34)   # altura de um dígito, a 1080
DIGIT_MAX_DIST = 0.12   # pior intra-classe medido: 0.048
DIGIT_MIN_MARGIN = 2.0  # o 2º melhor tem de estar ao menos 2x mais longe
DIGITS_JSON = os.path.join(_HERE, "digits.json")
UNKNOWN_DIR = os.path.join(_HERE, "unknown_digits")
LEVEL_MIN_AREA = 100  # área mínima de um dígito do research level

CHECK_HEX = "#e5474c"  # o visto vermelho
CHECK_TOL = 34
CHECK_MIN_RATIO = 0.015  # o visto ocupa ~5% da célula; isto é só contra ruído

BONUS_HEX = "#e5474c"  # os chevrons são do mesmo vermelho
BONUS_X = (170, 250)  # coluna do ícone de bónus, em 4K

_api = tesserocr.PyTessBaseAPI(
    path=settings.TESSDATA_PATH,
    psm=tesserocr.PSM.SINGLE_LINE,
    oem=tesserocr.OEM.LSTM_ONLY,
)


# ─────────────────────────────────────────────────────────────────────────────
# Canal com o free-play
# ─────────────────────────────────────────────────────────────────────────────


def _get(path: str) -> str:
    with urllib.request.urlopen(SERVER + path, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def wake() -> None:
    """Toque no stick direito: o ecrã apaga-se por inatividade e no Pokédex
    isto não muda nada, por isso é idempotente."""
    _get("/stick?side=right&x=0&y=1&secs=0.15")


def shot() -> np.ndarray:
    wake()
    time.sleep(0.25)
    _get("/shot?keep=0")
    im = cv2.imread(SHOT_PNG)
    if im is None:
        raise RuntimeError("não consegui ler " + SHOT_PNG)
    return im


# O /press segura a tecla 0.2s, o que chega para a lista disparar o auto-repeat
# e andar duas posições. Um toque curto anda exactamente uma.
STEP_SECS = 0.05


def step_down() -> None:
    _get(f"/hold?key=2&secs={STEP_SECS}")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de cor (os do capture.py, com o frame passado à mão)
# ─────────────────────────────────────────────────────────────────────────────


def has_color(im, box, hex_color: str, tol: int, min_ratio: float = 0.0) -> bool:
    x1, y1, x2, y2 = box
    if min_ratio <= 0:
        return capture._region_has_hex_color(x1, y1, x2, y2, hex_color, tol, frame=im)
    ratio = capture.region_color_ratio(x1, y1, x2, y2, hex_color, tol, frame=im)
    return ratio >= min_ratio


def _to4k(panel, box):
    """Converte coordenadas locais do recorte do painel para 4K.

    O factor sai do tamanho do próprio recorte, para não depender da
    resolução com que a placa de captura negociou.
    """
    fx = (PANEL[2] - PANEL[0]) / panel.shape[1]
    fy = (PANEL[3] - PANEL[1]) / panel.shape[0]
    x1, y1, x2, y2 = box
    return (
        PANEL[0] + int(x1 * fx),
        PANEL[1] + int(y1 * fy),
        PANEL[0] + int(x2 * fx),
        PANEL[1] + int(y2 * fy),
    )


def _ocr(img: np.ndarray, whitelist: str = "", psm=None) -> str:
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    g = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    _api.SetPageSegMode(psm or tesserocr.PSM.SINGLE_LINE)
    _api.SetVariable("tessedit_char_whitelist", whitelist)
    _api.SetImage(Image.fromarray(g))
    return _api.GetUTF8Text().strip()


# ─────────────────────────────────────────────────────────────────────────────
# A grelha das células, tirada dos pixels dos pills
# ─────────────────────────────────────────────────────────────────────────────


def _runs(mask) -> list[tuple[int, int]]:
    out, start = [], None
    for i, on in enumerate(mask):
        if on and start is None:
            start = i
        elif not on and start is not None:
            out.append((start, i - 1))
            start = None
    if start is not None:
        out.append((start, len(mask) - 1))
    return out


def cell_grid(panel: np.ndarray) -> list[list[tuple[int, int, int, int]]]:
    """As células (x1, y1, x2, y2) dos pills, linha a linha.

    As linhas saem do perfil vertical da cor dos pills; as colunas saem do
    perfil horizontal *dentro de cada linha*, o que absorve a inclinação. Uma
    tarefa com menos de 5 passos tem menos células, encostadas à direita — é o
    que se lê, não se assume nada.
    """
    b, g, r = (panel[:, :, i].astype(int) for i in range(3))
    pill = (b < 192) & (r - b > 40)

    bands = [
        (y1, y2)
        for y1, y2 in _runs(pill[:, PILLS_X[0] : PILLS_X[1]].sum(axis=1) > BAND_MIN_PX)
        if ROW_H[0] <= y2 - y1 + 1 <= ROW_H[1]
    ]

    grid = []
    for y1, y2 in bands:
        # Limiar baixo de propósito: o intervalo entre pills desce a 0, o corte
        # do dígito escuro só desce a ~0.3. É o que separa um do outro.
        frac = pill[y1 + 2 : y2 - 1, PILLS_X[0] : PILLS_X[1]].mean(axis=0)
        cols = [
            (x1 + PILLS_X[0], x2 + PILLS_X[0])
            for x1, x2 in _runs(frac > 0.15)
            if x2 - x1 + 1 >= PILL_W_MIN
        ]
        grid.append([(x1, y1, x2, y2) for x1, x2 in cols])
    return grid


# ─────────────────────────────────────────────────────────────────────────────
# Leitura do ecrã
# ─────────────────────────────────────────────────────────────────────────────


def read_tasks(im: np.ndarray) -> dict:
    """Vistos por linha, perguntando célula a célula se lá está o vermelho."""
    panel = capture.get_frame_region(*PANEL, frame=im)
    grid = cell_grid(panel)

    checks, cells, bonus, bands = [], [], [], []
    for row in grid:
        done = [
            has_color(im, _to4k(panel, c), CHECK_HEX, CHECK_TOL, CHECK_MIN_RATIO)
            for c in row
        ]
        checks.append(sum(done))
        cells.append(len(row))
        y1, y2 = row[0][1], row[0][3]
        bx = _to4k(panel, (0, y1, 0, y2))
        bands.append((bx[1], bx[3]))
        bonus.append(
            has_color(im, (BONUS_X[0], bx[1], BONUS_X[1], bx[3]), BONUS_HEX, CHECK_TOL, 0.2)
        )

    return {
        "checks": checks,
        "cells": cells,
        "bonus": bonus,
        "bands": bands,
        "n_tasks": len(grid),
    }


_dj = json.load(open(DIGITS_JSON, encoding="utf-8"))
DIGIT_SIZE = tuple(_dj["size"])  # (largura, altura) do bitmap normalizado
TEMPLATES = {
    int(d): np.array([c == "1" for c in bits]).reshape(
        DIGIT_SIZE[1], DIGIT_SIZE[0]
    )
    for d, bits in _dj["templates"].items()
}


def row_glyphs(im: np.ndarray, y1: int, y2: int) -> list[np.ndarray]:
    """Bitmaps normalizados dos dígitos de uma linha, da esquerda para a direita.

    Os glifos são o azul-marinho do texto; a barra separadora e os pontinhos
    das linhas ficam de fora por serem claros.
    """
    crop = capture.get_frame_region(
        VALUE_X[0], y1 - VALUE_PAD, VALUE_X[1], y2 + VALUE_PAD, frame=im
    )
    if crop.size == 0:
        return []
    b, g, r = (crop[:, :, k].astype(int) for k in range(3))
    dark = ((r < 140) & (g < 140) & (b < 160)).astype(np.uint8)
    n, _, stats, _ = cv2.connectedComponentsWithStats(dark, 8)
    found = sorted(
        (
            stats[i]
            for i in range(1, n)
            if stats[i][4] >= DIGIT_AREA_MIN and DIGIT_H[0] <= stats[i][3] <= DIGIT_H[1]
        ),
        key=lambda st: st[0],
    )
    out = []
    for x, y, w, h, _a in found:
        bm = dark[y : y + h, x : x + w].astype(np.uint8) * 255
        out.append(cv2.resize(bm, DIGIT_SIZE, interpolation=cv2.INTER_AREA) > 127)
    return out


def classify_digit(bm: np.ndarray) -> int | None:
    """O dígito mais parecido, ou None se não houver um vencedor claro."""
    dists = sorted((float((bm != t).mean()), d) for d, t in TEMPLATES.items())
    best, digit = dists[0]
    if best > DIGIT_MAX_DIST:
        return None
    if len(dists) > 1 and dists[1][0] < best * DIGIT_MIN_MARGIN:
        return None
    return digit


def read_values(im: np.ndarray, bands: list[tuple[int, int]]) -> tuple[list, list]:
    """O valor actual de cada tarefa, e os glifos que não soube classificar.

    Os glifos por classificar vão para fora para serem guardados e rotulados
    depois, sem obrigar a outra passagem pelo jogo.
    """
    values, unknown = [], []
    for i, (y1, y2) in enumerate(bands):
        glyphs = row_glyphs(im, y1, y2)
        digits = [classify_digit(bm) for bm in glyphs]
        if not glyphs or any(d is None for d in digits):
            values.append(None)
            unknown.extend(
                (i + 1, k, bm) for k, (bm, d) in enumerate(zip(glyphs, digits))
                if d is None
            )
        else:
            values.append(int("".join(str(d) for d in digits)))
    return values, unknown


def read_level(im: np.ndarray) -> int | None:
    """O número do Research Level, achando primeiro a caixa dos dígitos."""
    box = capture.get_frame_region(*LEVEL_BOX, frame=im)
    b, g, r = (box[:, :, i].astype(int) for i in range(3))
    dark = ((r < 140) & (g < 140) & (b < 160)).astype(np.uint8)
    n, _, stats, _ = cv2.connectedComponentsWithStats(dark, 8)
    digits = [stats[i] for i in range(1, n) if stats[i][4] > LEVEL_MIN_AREA]
    if not digits:
        return None
    pad = 6
    x1 = max(0, min(d[0] for d in digits) - pad)
    y1 = max(0, min(d[1] for d in digits) - pad)
    x2 = max(d[0] + d[2] for d in digits) + pad
    y2 = max(d[1] + d[3] for d in digits) + pad
    txt = _ocr(box[y1:y2, x1:x2], "0123456789", tesserocr.PSM.SINGLE_WORD)
    return int(txt) if txt.isdigit() and 1 <= int(txt) <= 10 else None


def count_check_blobs(im: np.ndarray) -> list[int]:
    """Segundo detector, independente do grid: conta as manchas do visto por
    banda. Serve só para confirmar o resultado do teste por célula."""
    panel = capture.get_frame_region(*PANEL, frame=im)
    b, g, r = (panel[:, :, i].astype(int) for i in range(3))
    mask = ((r > 150) & (g < 110) & (b < 110) & (r - g > 70)).astype(np.uint8)
    n, _, stats, cents = cv2.connectedComponentsWithStats(mask, 8)
    grid = cell_grid(panel)
    counts = [0] * len(grid)
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        cx, cy = cents[i]
        if not (90 <= area <= 260 and cx > PILLS_X[0] - 20):
            continue
        for k, row in enumerate(grid):
            if row and row[0][1] <= cy <= row[0][3]:
                counts[k] += 1
                break
    return counts


def read_list(im: np.ndarray) -> dict:
    """Que linha da lista está selecionada, o nome dela e se tem badge."""
    lit = [
        capture.is_pixel_color(x, y, SELECTED_HEX, SELECTED_TOL, frame=im)
        for x, y in LIST_ROW_PX
    ]
    if sum(lit) != 1:
        raise RuntimeError(f"esperava 1 linha selecionada na lista, vi {lit}")
    sel = lit.index(True)
    cy = LIST_ROW_PX[sel][1] + LIST_PX_TO_CENTER

    x1, dy1, x2, dy2 = NAME_BOX
    name = _ocr(capture.get_frame_region(x1, cy + dy1, x2, cy + dy2, frame=im))

    x1, dy1, x2, dy2 = BADGE_BOX
    badge = has_color(
        im, (x1, cy + dy1, x2, cy + dy2), BADGE_HEX, BADGE_TOL, BADGE_MIN_RATIO
    )
    return {"row": sel + 1, "name_ocr": name, "badge": badge}


# ─────────────────────────────────────────────────────────────────────────────
# Nomes: fuzzy match contra a dex do game8
# ─────────────────────────────────────────────────────────────────────────────

_NAMES = [m["name"] for m in json.load(open(DEX_INDEX, encoding="utf-8"))]


def match_name(raw: str) -> tuple[str | None, float]:
    txt = "".join(c for c in raw if c.isalnum() or c in " -.'").strip()
    if not txt:
        return None, 0.0
    best, score = None, 0.0
    for n in _NAMES:
        s = difflib.SequenceMatcher(None, txt.lower(), n.lower()).ratio()
        if len(txt) >= 5 and n.lower().startswith(txt.lower()[:5]):
            s = max(s, 0.85)  # o OCR às vezes cola lixo no fim do nome
        if s > score:
            best, score = n, s
    return best, round(score, 3)


# ─────────────────────────────────────────────────────────────────────────────
# Ler um mon, com as verificações
# ─────────────────────────────────────────────────────────────────────────────


def read_mon(im: np.ndarray) -> dict:
    lst = read_list(im)
    tasks = read_tasks(im)
    values, unknown = read_values(im, tasks["bands"])
    level = read_level(im)
    name, score = match_name(lst["name_ocr"])

    problems = []
    if tasks["n_tasks"] == 0:
        problems.append("nenhuma tarefa detectada")
    if tasks["n_tasks"] > 9:
        problems.append(f"{tasks['n_tasks']} tarefas (o game8 tem no máximo 9)")
    if any(c > 5 for c in tasks["cells"]):
        problems.append(f"células por linha fora do esperado: {tasks['cells']}")

    if unknown:
        problems.append(
            "dígitos por rotular nas linhas "
            f"{sorted({row for row, _pos, _bm in unknown})}"
        )
    # com k passos feitos o valor já passou o patamar k, logo é >= k
    low = [
        i + 1
        for i, (v, c) in enumerate(zip(values, tasks["checks"]))
        if v is not None and v < c
    ]
    if low:
        problems.append(f"valor menor que os checks nas linhas {low}")

    blobs = count_check_blobs(im)
    if blobs != tasks["checks"]:
        problems.append(f"os dois detectores discordam: célula={tasks['checks']} blob={blobs}")

    if name is None or score < 0.55:
        problems.append(f"nome incerto: {lst['name_ocr']!r} -> {name} ({score})")

    # Checksum: 10 pontos por passo, 20 nas tarefas de bónus, nível = pontos/10
    units = sum(c * (2 if bn else 1) for c, bn in zip(tasks["checks"], tasks["bonus"]))
    expected = min(10, units)
    if level is None:
        problems.append("não li o research level")
    elif level != expected:
        problems.append(f"checksum: ecrã diz {level}, os checks dão {expected}")

    return {
        "mon": name,
        "name_ocr": lst["name_ocr"],
        "name_score": score,
        "research_level": level,
        "complete": lst["badge"],
        "list_row": lst["row"],
        "unknown_digits": unknown,
        "checks": tasks["checks"],
        "values": values,
        "cells": tasks["cells"],
        "problems": problems,
    }


def panel_key(im: np.ndarray) -> bytes:
    """Impressão digital do painel + lista, para apanhar um d-pad perdido."""
    a = capture.get_frame_region(*PANEL, frame=im)
    b = capture.get_frame_region(*LIST, frame=im)
    return cv2.resize(a, (66, 42)).tobytes() + cv2.resize(b, (46, 72)).tobytes()


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────


def _fmt(r: dict) -> str:
    cells = " ".join(
        f"{v if v is not None else '?'}:{c}" for c, v in zip(r["checks"], r["values"])
    )
    tag = " COMPLETO" if r["complete"] else ""
    bad = ("  !! " + "; ".join(r["problems"])) if r["problems"] else ""
    return f"{str(r['mon']):20} lvl {str(r['research_level']):>2}{tag}  [{cells}]{bad}"


def _dump_unknown(r: dict) -> None:
    """Guarda os glifos que o matcher não reconheceu, para eu os rotular.

    Ficam em PNG (para se ver que dígito é) e em .npy (para se recalcular o
    valor sem ter de voltar ao jogo).
    """
    if not r.get("unknown_digits"):
        return
    os.makedirs(UNKNOWN_DIR, exist_ok=True)
    for row, pos, bm in r["unknown_digits"]:
        stem = os.path.join(UNKNOWN_DIR, f"{r['mon']}-L{row}-{pos}")
        cv2.imwrite(stem + ".png", bm.astype(np.uint8) * 255)
        np.save(stem + ".npy", bm)


def _save(results: list[dict]) -> None:
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(
            [{k: r[k] for k in OUT_KEYS} for r in results],
            fh, ensure_ascii=False, indent=1,
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=300)
    ap.add_argument("--shot", help="lê um png já gravado e sai")
    args = ap.parse_args()

    if args.shot:
        im = cv2.imread(args.shot)
        if im is None:
            raise SystemExit(f"não consegui ler {args.shot}")
        r = read_mon(im)
        print(_fmt(r))
        print(json.dumps({k: r[k] for k in OUT_KEYS}, ensure_ascii=False))
        return

    os.makedirs(FAIL_DIR, exist_ok=True)
    results: list[dict] = []
    seen: set[bytes] = set()

    try:
        _walk(args, results, seen)
    finally:
        _save(results)
        bad = [r for r in results if r["problems"]]
        print(
            f"\n{len(results)} mons incompletos lidos, "
            f"{len(bad)} com avisos -> {OUT_JSON}"
        )
        for r in bad:
            print(f"   {r['mon']}: {'; '.join(r['problems'])}")


def _walk(args, results: list[dict], seen: set[bytes]) -> None:
    for i in range(args.max):
        if i:
            step_down()
            time.sleep(0.35)
        im = shot()

        key = panel_key(im)
        if key in seen:
            step_down()  # o d-pad não registou; tentar outra vez
            time.sleep(0.5)
            im = shot()
            key = panel_key(im)
            if key in seen:
                cv2.imwrite(os.path.join(FAIL_DIR, f"repetiu-{i:03d}.png"), im)
                print("!! o ecrã repetiu-se duas vezes — paro aqui")
                break
        seen.add(key)

        try:
            r = read_mon(im)
        except RuntimeError as exc:
            time.sleep(0.5)
            im = shot()
            try:
                r = read_mon(im)
            except RuntimeError as exc2:
                path = os.path.join(FAIL_DIR, f"erro-{i:03d}.png")
                cv2.imwrite(path, im)
                print(f"!! {exc2} (antes: {exc}) — guardei {path}")
                break

        if r["problems"]:
            # relê uma vez: quase sempre é o painel apanhado a meio da animação
            time.sleep(0.4)
            again = read_mon(shot())
            if not again["problems"]:
                r = again
            else:
                path = os.path.join(FAIL_DIR, f"aviso-{i:03d}.png")
                cv2.imwrite(path, im)
                r = again
                r["shot"] = path
        _dump_unknown(r)

        if r["complete"]:
            print(f"     {r['mon']} já tem pokébola — a lista está ordenada por "
                  "research level, portanto os incompletos acabaram aqui.")
            break

        results.append(r)
        print(f"{i + 1:3}. {_fmt(r)}", flush=True)


if __name__ == "__main__":
    main()
