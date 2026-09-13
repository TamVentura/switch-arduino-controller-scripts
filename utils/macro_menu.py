"""
macro_menu.py — the terminal side of the macro runner.

Reads a line, decides what it means, draws the list.  The list is redrawn on
every command rather than animating itself: the prompt sits blocked in
input(), so there is nothing to animate behind it.  Press Enter to see where a
loop has got to.

    ── macros ──────────────────────────────────────────
       1  abrir
     ▶ 2  farmar                          loop, volta 12
       3  sair
    ────────────────────────────────────────────────────
     <n> correr/parar   l<n> em loop   s parar
     n nova   e<n> editar   d<n> apagar   Enter refresca   q sair
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from colorama import Fore, Style

RUNNING_MARK = "▶"

# Single letters that mean something on their own.
_STANDALONE = {"n": "new", "s": "stop", "q": "quit"}

# Letter prefixes that take a macro number.
_PREFIXED = {"l": "toggle", "e": "edit", "d": "delete"}


@dataclass(frozen=True)
class Action:
    """What a line of input turned out to mean."""

    kind: str
    name: str | None = None
    loop: bool = False
    message: str | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Reading a line
# ─────────────────────────────────────────────────────────────────────────────


def parse_choice(text: str, names: list[str]) -> Action:
    """Turn what was typed into an Action, against the macros that exist."""
    choice = text.strip().lower()

    if not choice:
        return Action("refresh")

    if choice in _STANDALONE:
        return Action(_STANDALONE[choice])

    prefix, digits = ("", choice)
    if choice[0] in _PREFIXED:
        prefix, digits = choice[0], choice[1:]

    if not digits.isdigit():
        return Action("error", message=f"nao percebi {text.strip()!r}")

    index = int(digits)
    if not 1 <= index <= len(names):
        return Action(
            "error",
            message=f"nao ha macro numero {index} (existem {len(names)})",
        )

    name = names[index - 1]
    if prefix == "l":
        return Action("toggle", name=name, loop=True)
    if prefix:
        return Action(_PREFIXED[prefix], name=name)
    return Action("toggle", name=name)


# ─────────────────────────────────────────────────────────────────────────────
# Drawing the list
# ─────────────────────────────────────────────────────────────────────────────

_WIDTH = 56


def render(names: list[str], status: dict[str, Any] | None) -> list[str]:
    """Return the lines of the macro list, marking the one that is running."""
    lines = [f"{Fore.CYAN}── macros {'─' * (_WIDTH - 10)}{Style.RESET_ALL}"]

    if not names:
        lines.append(
            f"  {Fore.YELLOW}sem macros ainda — 'n' para escrever a primeira"
            f"{Style.RESET_ALL}"
        )
    for number, name in enumerate(names, start=1):
        running = bool(status and status["name"] == name)
        mark = f"{Fore.GREEN}{RUNNING_MARK}{Style.RESET_ALL}" if running else " "
        label = f"{Fore.GREEN}{name}{Style.RESET_ALL}" if running else name
        note = _note(status) if running and status else ""
        padding = max(1, _WIDTH - 8 - len(name) - len(_plain(note)))
        lines.append(f" {mark} {number:>2}  {label}{' ' * padding}{note}")

    lines.append(f"{Fore.CYAN}{'─' * _WIDTH}{Style.RESET_ALL}")
    lines.append(
        f" {Fore.WHITE}<n>{Style.RESET_ALL} correr/parar   "
        f"{Fore.WHITE}l<n>{Style.RESET_ALL} em loop   "
        f"{Fore.WHITE}s{Style.RESET_ALL} parar"
    )
    lines.append(
        f" {Fore.WHITE}n{Style.RESET_ALL} nova   "
        f"{Fore.WHITE}e<n>{Style.RESET_ALL} editar   "
        f"{Fore.WHITE}d<n>{Style.RESET_ALL} apagar   "
        f"{Fore.WHITE}Enter{Style.RESET_ALL} refresca   "
        f"{Fore.WHITE}q{Style.RESET_ALL} sair"
    )
    return lines


def _note(status: dict[str, Any]) -> str:
    if status.get("loop"):
        return f"{Fore.GREEN}loop, volta {status.get('lap', 1)}{Style.RESET_ALL}"
    return f"{Fore.GREEN}a correr{Style.RESET_ALL}"


def _plain(text: str) -> str:
    """The text without colour codes, for measuring how wide it prints."""
    out, skipping = [], False
    for char in text:
        if char == "\033":
            skipping = True
        elif skipping:
            skipping = char != "m"
        else:
            out.append(char)
    return "".join(out)
