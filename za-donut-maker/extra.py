import difflib
import json
import os
import re
import sys
import unicodedata
from typing import NotRequired, TypedDict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import time

import numpy as np

from switch import *
from pprint import pprint

with open(
    os.path.join(os.path.dirname(__file__), "donuts_list.json"), encoding="utf-8"
) as f:
    BERRY_LIST = json.load(f)

from powers import powers_texts

# --- tolerancia a misreads do OCR -------------------------------------------
# O tesseract troca letras parecidas ("Item" -> "ltem", "Power" -> "Pcwer",
# "Shiny" -> "Shlny"), por isso comparar texto exato falha. Normalizamos as
# confusoes mais comuns e comparamos por semelhanca, nao por igualdade.

_OCR_CONFUSIONS = str.maketrans(
    {
        "0": "o",
        "1": "i",
        "2": "z",
        "5": "s",
        "6": "g",
        "8": "b",
        "l": "i",
        "|": "i",
        "!": "i",
        "$": "s",
        "@": "a",
    }
)

# abaixo disto e mais provavel ser outra power do que a certa mal lida
_POWER_MATCH_THRESHOLD = 0.75

# o extra sao palavras inteiras e curtas ("fire", "berries"), da para exigir mais
_EXTRA_MATCH_THRESHOLD = 0.8

# digitos que o OCR devolve como letras (para o nivel)
_DIGIT_LOOKALIKE = {
    "o": "0",
    "q": "0",
    "i": "1",
    "l": "1",
    "|": "1",
    "z": "2",
    "s": "5",
    "t": "7",
    "b": "8",
    "g": "9",
}

# "Lv. 3", "Lv 3", "LV.3", "Ly. 3" (o "v" tambem se le mal)
_LEVEL_RE = re.compile(r"\bl\s*[vy]\s*[.,:]?\s*([0-9a-z|])", re.IGNORECASE)


def _normalize(text: str) -> str:
    """Reduz o texto a so letras, colapsando as trocas tipicas do OCR."""
    text = unicodedata.normalize("NFKD", text).lower()
    text = text.replace("rn", "m").replace("vv", "w")
    text = text.translate(_OCR_CONFUSIONS)
    return re.sub(r"[^a-z]", "", text)


def _similarity(needle: str, haystack: str) -> float:
    """Semelhanca do needle contra o inicio do haystack.

    A linha lida traz mais do que o nome ("Item Power: Berries Lv. 3"), por
    isso compara-se so um prefixo do tamanho do nome (com folga, o OCR come e
    inventa letras). Ancorar no inicio evita apanhar uma power que nao esta na
    tabela pelo meio da linha ("Encounter Power" ~ "Item Power").
    """
    if not needle or not haystack:
        return 0.0
    best = 0.0
    for start in (0, 1):
        for size in range(max(1, len(needle) - 2), len(needle) + 3):
            window = haystack[start : start + size]
            if not window:
                continue
            best = max(best, difflib.SequenceMatcher(None, needle, window).ratio())
            if best == 1.0:
                return best
    return best


def _power_core(power_text: str) -> str:
    """A parte que distingue a power das outras.

    Quase todas acabam em "Power", e esse sufixo comum faz qualquer linha
    parecer-se com qualquer power ("Egg Power" ~ "Item Power"). Fora ele,
    sobra so o nome, que e o que decide.
    """
    normalized = _normalize(power_text)
    core = re.sub(r"power$", "", normalized)
    return core or normalized


def _match_power(line: str) -> str | None:
    """A power mais parecida com a linha lida, ou None se nenhuma chega ao minimo."""
    haystack = _normalize(line)
    if not haystack:
        return None

    best_name, best_score = None, 0.0
    for power_name, power_text in powers_texts.items():
        score = _similarity(_power_core(power_text), haystack)
        if score > best_score:
            best_name, best_score = power_name, score

    if best_score < _POWER_MATCH_THRESHOLD:
        print(
            f"{Fore.YELLOW}Power nao reconhecida:{Style.RESET_ALL} {line!r} "
            f"{Fore.LIGHTBLACK_EX}(melhor: {best_name} @ {best_score:.2f})"
            f"{Style.RESET_ALL}\n"
        )
        return None
    return best_name


def _parse_level(line: str) -> int | None:
    match = _LEVEL_RE.search(line)
    if not match:
        return None
    digit = _DIGIT_LOOKALIKE.get(match.group(1).lower(), match.group(1))
    if not digit.isdigit():
        return None
    level = int(digit)
    return level if 1 <= level <= 3 else None


def _parse_extra(line: str) -> str | None:
    # o ":" tambem sai como ";", e nem sempre traz espaco a seguir
    match = re.search(r"[:;]\s*(.+)", line)
    if not match:
        return None
    extra = match.group(1).split("(")[0]
    extra = _LEVEL_RE.sub("", extra)  # tira o "Lv. 3" colado ao extra
    extra = extra.strip(" .,:;-").lower()
    return extra or None


def _same_extra(expected: str | list[str] | None, actual: str | None) -> bool:
    """Compara o extra pedido com o lido, tolerando misreads.

    O pedido pode ser um texto ("fire"), varios em alternativa ("water" ou
    "dragon" ou "ground") ou nada — e nada quer dizer que qualquer extra serve.
    Com varios basta um bater.

    O extra e texto livre do jogo ("Berries", "Fire"), por isso sofre das
    mesmas trocas de letras que o nome da power: "Berrles", "Berrjes", "Flre".
    Compara-se pelo inicio, para aguentar lixo colado no fim.
    """
    wanted = [expected] if isinstance(expected, str) else list(expected or [])
    wanted = [text for text in wanted if text]
    if not wanted:
        return True  # nao se pediu tipo nenhum: serve o que vier
    if actual is None:
        return False
    return any(_matches_extra(text, actual) for text in wanted)


def _matches_extra(expected: str, actual: str) -> bool:
    expected_normalized = _normalize(expected)
    actual_normalized = _normalize(actual)
    if expected_normalized == actual_normalized:
        return True
    return _similarity(expected_normalized, actual_normalized) >= _EXTRA_MATCH_THRESHOLD


class Power(TypedDict):
    power: str
    level: NotRequired[int]
    # o esperado pode trazer varios em alternativa; o lido do OCR e sempre um
    extra: NotRequired[str | list[str] | None]
