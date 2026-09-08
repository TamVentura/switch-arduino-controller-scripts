"""Menus de consola: escolher com as setas + enter, em vez de escrever.

Fica num modulo proprio porque nao tem nada de donuts — so leitura de teclas e
desenho das opcoes. O main.py junta-lhe o significado (receitas, argumentos).
"""

import sys

from colorama import Fore, Style

try:  # so existe no Windows; sem ele cai-se no menu numerado
    import msvcrt
except ImportError:  # pragma: no cover
    msvcrt = None

# no Windows as teclas especiais chegam em dois getwch: prefixo + codigo
_SPECIAL_PREFIXES = ("\x00", "\xe0")
_SPECIAL_KEYS = {"H": "up", "P": "down", "K": "left", "M": "right"}


def _interactive() -> bool:
    return msvcrt is not None and sys.stdin is not None and sys.stdin.isatty()


def _read_key() -> str:
    """Uma tecla, ja traduzida ("up", "down", "enter", "escape" ou o caracter)."""
    char = msvcrt.getwch()
    if char in _SPECIAL_PREFIXES:
        return _SPECIAL_KEYS.get(msvcrt.getwch(), "")
    if char in ("\r", "\n"):
        return "enter"
    if char == "\x1b":
        return "escape"
    if char in ("\x03", "\x04"):  # Ctrl+C / Ctrl+D
        raise KeyboardInterrupt()
    return char


def _draw(options: list[tuple], index: int) -> None:
    for i, (_, label) in enumerate(options):
        color = Fore.LIGHTGREEN_EX if i == index else Fore.LIGHTBLACK_EX
        marker = ">" if i == index else " "
        # o 2K limpa a linha toda: as opcoes tem larguras diferentes
        sys.stdout.write(f"\x1b[2K  {color}{marker} {label}{Style.RESET_ALL}\n")
    sys.stdout.flush()


def _select_numbered(title: str, options: list[tuple], default: int):
    """Alternativa quando nao ha consola interativa (pipe, redirect)."""
    print(f"{Fore.CYAN}{title}{Style.RESET_ALL}")
    for i, (_, label) in enumerate(options, 1):
        print(f"  {i}. {label}")
    while True:
        answer = input(f"Escolha [{default + 1}]: ").strip()
        if not answer:
            return options[default][0]
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return options[int(answer) - 1][0]
        print(f"{Fore.LIGHTRED_EX}Escolha entre 1 e {len(options)}.{Style.RESET_ALL}")


def select(title: str, options: list[tuple], default: int = 0):
    """Mostra as opcoes e devolve o valor da escolhida.

    ``options`` sao pares ``(valor, texto)``. Setas para andar, enter para
    escolher, esc para desistir (levanta ``KeyboardInterrupt``).
    """
    if not options:
        raise ValueError("select() sem opcoes")
    default = max(0, min(default, len(options) - 1))

    if not _interactive():
        return _select_numbered(title, options, default)

    print(
        f"{Fore.CYAN}{title}{Style.RESET_ALL} "
        f"{Fore.LIGHTBLACK_EX}(setas + enter){Style.RESET_ALL}"
    )
    index = default
    _draw(options, index)
    while True:
        key = _read_key()
        if key == "up":
            index = (index - 1) % len(options)
        elif key == "down":
            index = (index + 1) % len(options)
        elif key == "enter":
            return options[index][0]
        elif key == "escape":
            raise KeyboardInterrupt()
        else:
            continue
        sys.stdout.write(f"\x1b[{len(options)}A")  # volta ao topo da lista
        _draw(options, index)


def confirm(question: str, default: bool = True) -> bool:
    return select(question, [(True, "Sim"), (False, "Nao")], 0 if default else 1)


def ask_text(prompt: str, required: bool = True, default: str | None = None) -> str | None:
    suffix = f" [{default}]" if default else ""
    while True:
        answer = input(f"{Fore.CYAN}{prompt}{suffix}: {Style.RESET_ALL}").strip()
        if answer:
            return answer
        if default:
            return default
        if not required:
            return None
        print(f"{Fore.LIGHTRED_EX}Nao pode ficar vazio.{Style.RESET_ALL}")


def ask_int(prompt: str, default: int = 1, minimum: int = 1) -> int:
    while True:
        answer = input(f"{Fore.CYAN}{prompt} [{default}]: {Style.RESET_ALL}").strip()
        if not answer:
            return default
        if answer.isdigit() and int(answer) >= minimum:
            return int(answer)
        print(f"{Fore.LIGHTRED_EX}Escreve um numero >= {minimum}.{Style.RESET_ALL}")
