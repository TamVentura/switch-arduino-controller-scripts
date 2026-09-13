"""
macros.py — the little language used to write macros by hand.

One line, one command.  Text in, a list of `Command` out; nothing here talks to
the Arduino, so a macro can be written and checked without the hardware being
plugged in (which is the whole point: the capture card belongs to Elgato
Studio while you play, so there is no vision to lean on).

    click A                 # one press, controller defaults
    click A x3 sleep=0.3    # three presses, 0.3 s between them
    hold B
    wait 1.5
    release B
    hold_for R 2
    stick left 0 -1
    stick left 0 0

Lines starting with `#` are comments; blank lines are ignored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ─────────────────────────────────────────────────────────────────────────────
# Commands
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Command:
    """One parsed instruction: an op name and its already-validated arguments."""

    op: str
    args: dict[str, Any] = field(default_factory=dict)


class MacroError(Exception):
    """Raised when a macro text does not parse.  Carries every bad line."""


# ─────────────────────────────────────────────────────────────────────────────
# Keys
# ─────────────────────────────────────────────────────────────────────────────

# Every character the Arduino sketch accepts as a press/release target.
# Case matters: 'l'/'r' are ZL/ZR while 'L'/'R' are the bumpers, and the
# lowercase letters are the 8-way digital stick pushes.
KEYS: frozenset[str] = frozenset(
    "ABXYH+-LRlrC{}"  # buttons
    "1234"  # d-pad: right, down, left, up
    "wasdqezc"  # left stick, 8 directions
    "uhjkyinm"  # right stick, 8 directions
)

# Friendlier names for the keys worth naming.  Looked up case-insensitively,
# and only for multi-character words, so they can never shadow a real key.
ALIASES: dict[str, str] = {
    "up": "4",
    "down": "2",
    "left": "3",
    "right": "1",
    "zl": "l",
    "zr": "r",
    "home": "H",
    "capture": "C",
    "plus": "+",
    "minus": "-",
    "lclick": "{",
    "rclick": "}",
}


def _resolve_key(token: str) -> str:
    """Return the Arduino character for a key written in a macro."""
    if token in KEYS:
        return token
    return ALIASES[token.lower()]


# ─────────────────────────────────────────────────────────────────────────────
# Parsing
# ─────────────────────────────────────────────────────────────────────────────


class _LineError(Exception):
    """One bad line, without the line number — parse() prefixes that."""


def parse(text: str) -> list[Command]:
    """Turn macro text into a list of commands.

    Every line is checked before anything is returned, so a macro either parses
    whole or not at all: a typo can never reach the Arduino half way through.
    Raises MacroError listing every bad line, numbered against the text as it
    was written (comments and blank lines included).
    """
    commands: list[Command] = []
    errors: list[str] = []

    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        try:
            commands.append(_parse_line(line.split()))
        except _LineError as exc:
            errors.append(f"linha {number}: {exc}")

    if errors:
        raise MacroError("\n".join(errors))
    return commands


# ─────────────────────────────────────────────────────────────────────────────
# One line at a time
# ─────────────────────────────────────────────────────────────────────────────

_SIGNATURES: dict[str, str] = {
    "click": "click TECLA [xN] [sleep=S] [hold=H]",
    "hold": "hold TECLA",
    "release": "release TECLA",
    "hold_for": "hold_for TECLA SEGUNDOS",
    "stick": "stick left|right X Y",
    "wait": "wait SEGUNDOS",
}


def _parse_line(tokens: list[str]) -> Command:
    op, rest = tokens[0], tokens[1:]

    if op not in _SIGNATURES:
        raise _LineError(
            f"comando desconhecido {op!r} "
            f"(conhecidos: {', '.join(sorted(_SIGNATURES))})"
        )

    if op == "click":
        return _parse_click(rest)
    if op in ("hold", "release"):
        _expect(op, rest, 1)
        return Command(op, {"key": _key(rest[0])})
    if op == "hold_for":
        _expect(op, rest, 2)
        return Command(
            "hold_for",
            {"key": _key(rest[0]), "duration": _seconds(rest[1], "a duracao")},
        )
    if op == "wait":
        _expect(op, rest, 1)
        return Command("wait", {"seconds": _seconds(rest[0], "a espera")})

    _expect(op, rest, 3)
    side = rest[0].lower()
    if side not in ("left", "right"):
        raise _LineError(f"stick tem de ser left ou right, e nao {rest[0]!r}")
    return Command(
        "stick",
        {"side": side, "x": _axis(rest[1]), "y": _axis(rest[2])},
    )


def _parse_click(rest: list[str]) -> Command:
    if not rest:
        raise _LineError(f"falta a tecla — {_SIGNATURES['click']}")

    # Defaults mirror controller.click(), so a bare `click A` behaves exactly
    # like it does everywhere else in the project.
    args: dict[str, Any] = {
        "key": _key(rest[0]),
        "sleep": 0.5,
        "repeat": 1,
        "hold": 0.1,
    }

    for token in rest[1:]:
        if token.startswith("x"):
            args["repeat"] = _repeat(token)
        elif token.startswith("sleep="):
            args["sleep"] = _seconds(token[len("sleep=") :], "sleep")
        elif token.startswith("hold="):
            args["hold"] = _seconds(token[len("hold=") :], "hold")
        else:
            raise _LineError(
                f"parametro desconhecido {token!r} — {_SIGNATURES['click']}"
            )

    return Command("click", args)


# ─────────────────────────────────────────────────────────────────────────────
# Token checkers
# ─────────────────────────────────────────────────────────────────────────────


def _expect(op: str, rest: list[str], count: int) -> None:
    if len(rest) != count:
        raise _LineError(
            f"{op} leva {count} argumento(s) e recebeu {len(rest)} "
            f"— {_SIGNATURES[op]}"
        )


def _key(token: str) -> str:
    try:
        return _resolve_key(token)
    except KeyError:
        raise _LineError(
            f"tecla desconhecida {token!r} "
            f"(atencao: 'l'/'r' sao o ZL/ZR e 'L'/'R' os bumpers; "
            f"nomes validos: {', '.join(sorted(ALIASES))})"
        ) from None


def _seconds(token: str, what: str) -> float:
    try:
        value = float(token)
    except ValueError:
        raise _LineError(f"{what} tem de ser um numero, e nao {token!r}") from None
    if value < 0:
        raise _LineError(f"{what} nao pode ser negativa ({token})")
    return value


def _repeat(token: str) -> int:
    try:
        value = int(token[1:])
    except ValueError:
        raise _LineError(
            f"repeticoes escrevem-se xN com N inteiro, e nao {token!r}"
        ) from None
    if value < 1:
        raise _LineError(f"tem de haver pelo menos uma repeticao ({token})")
    return value


def _axis(token: str) -> float:
    try:
        value = float(token)
    except ValueError:
        raise _LineError(f"o eixo tem de ser um numero, e nao {token!r}") from None
    if not -1.0 <= value <= 1.0:
        raise _LineError(f"o eixo vai de -1 a 1, e {token} esta fora")
    return value
