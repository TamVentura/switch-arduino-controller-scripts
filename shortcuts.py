"""
shortcuts.py — jogar com o comando e correr macros, sem visão nenhuma.

    python shortcuts.py
    python shortcuts.py COM8      # forçar o porto série

Ao contrário de tudo o resto no projeto, este arranque **não abre a placa de
captura**.  Só um processo pode ter o Elgato, e enquanto jogas é o Elgato
Studio que o tem — é de lá que vem a imagem, sem o atraso da janela de
OpenCV.  Fica de fora, portanto, tudo o que dependa de ver o ecrã: nada de
OCR, nada de templates, nada de esperar que uma caixa de texto apareça.  As
macros correm às cegas, pelo relógio.

O que arranca:

  * o passthrough do comando Xbox → Arduino → Switch, como sempre;
  * um menu no terminal para escrever, gravar e disparar macros.

No menu, `<n>` liga e desliga: carregas uma vez para a macro arrancar, e
outra vez no mesmo número para a parar.  A lista marca com ▶ a que está a
correr.  Como o prompt fica bloqueado no input(), a marca não se anima
sozinha — carrega Enter para a lista voltar a aparecer actualizada.

A linguagem das macros está em utils/macros.py; em resumo:

    click A                 # um toque
    click A x3 sleep=0.3    # três toques, 0.3 s entre eles
    hold B                  # carrega e deixa carregado
    wait 1.5
    release B
    hold_for R 2            # carrega dois segundos e larga
    stick left 0 -1         # empurra o stick esquerdo para cima
    stick left 0 0          # e devolve-o ao centro
"""

from __future__ import annotations

import os
import subprocess
import sys
import webbrowser

_ROOT = os.path.dirname(os.path.abspath(__file__))
for _p in (_ROOT, os.path.join(_ROOT, "utils")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import controller  # noqa: E402
import macro_menu  # noqa: E402
import macro_server  # noqa: E402
import settings  # noqa: E402
import xbox_passthrough  # noqa: E402
from colorama import Fore, Style  # noqa: E402
from colorama import init as _colorama_init
from macro_runner import MacroRunner  # noqa: E402
from macros import MacroError  # noqa: E402

_colorama_init()

# A lista usa ─ e ▶.  Numa consola em cp1252 isso rebenta o print com
# UnicodeEncodeError, por isso força-se UTF-8 e, se ainda assim não der para
# desenhar um caracter, sai um '?' em vez de o menu ir abaixo.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Escrever macros
# ─────────────────────────────────────────────────────────────────────────────


def _type_macro() -> str | None:
    """Ler uma macro linha a linha.  Termina com '.' sozinho; vazio cancela."""
    print(
        f"{Fore.CYAN}Escreve a macro, uma linha por comando. "
        f"'.' sozinho termina, Ctrl+C cancela.{Style.RESET_ALL}"
    )
    lines: list[str] = []
    while True:
        try:
            line = input(f"{Fore.CYAN}│{Style.RESET_ALL} ")
        except (KeyboardInterrupt, EOFError):
            print(f"\n{Fore.YELLOW}Cancelado.{Style.RESET_ALL}")
            return None
        if line.strip() == ".":
            break
        lines.append(line)
    text = "\n".join(lines).strip()
    return text or None


def _edit_in_editor(path) -> None:
    """Abrir a macro no editor e esperar que o fechem."""
    editor = os.environ.get("EDITOR")
    try:
        if editor:
            subprocess.run([editor, str(path)], check=False)
        else:
            subprocess.run(["notepad.exe", str(path)], check=False)
    except Exception as exc:
        print(f"{Fore.RED}Não consegui abrir o editor: {exc}{Style.RESET_ALL}")


def _ask_name(runner: MacroRunner) -> str | None:
    try:
        name = input(f"{Fore.CYAN}Nome da macro:{Style.RESET_ALL} ").strip()
    except (KeyboardInterrupt, EOFError):
        return None
    if not name:
        return None
    if runner.store.exists(name):
        try:
            answer = input(f"{Fore.YELLOW}Já existe. Substituir? [s/N]{Style.RESET_ALL} ")
        except (KeyboardInterrupt, EOFError):
            return None
        if answer.strip().lower() not in ("s", "sim", "y"):
            return None
    return name


def _new_macro(runner: MacroRunner) -> None:
    text = _type_macro()
    if text is None:
        return
    name = _ask_name(runner)
    if name is None:
        print(f"{Fore.YELLOW}Não gravei.{Style.RESET_ALL}")
        return
    try:
        path = runner.store.save(name, text + "\n")
    except (MacroError, ValueError) as exc:
        print(f"{Fore.RED}{exc}{Style.RESET_ALL}")
        print(f"{Fore.YELLOW}Não gravei — corrige e escreve outra vez.{Style.RESET_ALL}")
        return
    print(f"{Fore.GREEN}Gravada em {path}{Style.RESET_ALL}")


def _edit_macro(runner: MacroRunner, name: str) -> None:
    path = runner.store.path_for(name)
    print(f"{Fore.CYAN}A abrir {path} no editor…{Style.RESET_ALL}")
    _edit_in_editor(path)
    try:
        runner.store.commands(name)
    except MacroError as exc:
        print(f"{Fore.RED}A macro ficou com erros:{Style.RESET_ALL}\n{exc}")
        print(f"{Fore.YELLOW}Está gravada na mesma; 'e' outra vez para corrigir.{Style.RESET_ALL}")
        return
    print(f"{Fore.GREEN}{name} ok.{Style.RESET_ALL}")


def _delete_macro(runner: MacroRunner, name: str) -> None:
    try:
        answer = input(f"{Fore.YELLOW}Apagar {name}? [s/N]{Style.RESET_ALL} ")
    except (KeyboardInterrupt, EOFError):
        return
    if answer.strip().lower() not in ("s", "sim", "y"):
        return
    runner.store.delete(name)
    print(f"{Fore.GREEN}{name} apagada.{Style.RESET_ALL}")


# ─────────────────────────────────────────────────────────────────────────────
# Menu
# ─────────────────────────────────────────────────────────────────────────────


def _toggle(runner: MacroRunner, name: str, loop: bool) -> None:
    current = runner.status()
    if current and current["name"] != name:
        print(f"{Fore.YELLOW}A parar {current['name']}…{Style.RESET_ALL}")
    try:
        started = runner.toggle(name, loop=loop)
    except MacroError as exc:
        print(f"{Fore.RED}{name} não parseia:{Style.RESET_ALL}\n{exc}")
        return
    if started:
        mode = "em loop" if loop else "uma vez"
        print(
            f"{Fore.GREEN}▶ {name} ({mode}) — o comando fica calado "
            f"até parar.{Style.RESET_ALL}"
        )
    # A paragem não se anuncia aqui: quem anuncia o fim é sempre o _on_finish,
    # para sair uma linha só, venha a ordem do terminal ou da web.


def _menu(runner: MacroRunner, web_url: str | None) -> None:
    while True:
        print()
        for line in macro_menu.render(runner.store.names(), runner.status()):
            print(line)

        try:
            typed = input(f"{Fore.CYAN}>{Style.RESET_ALL} ")
        except (KeyboardInterrupt, EOFError):
            # Ctrl+C pára a macro; com nada a correr, sai.
            if runner.is_running():
                print()
                runner.stop()
                continue
            print()
            return

        action = macro_menu.parse_choice(typed, runner.store.names())

        if action.kind == "quit":
            return
        if action.kind == "refresh":
            continue
        if action.kind == "error":
            print(f"{Fore.RED}{action.message}{Style.RESET_ALL}")
        elif action.kind == "stop":
            if runner.is_running():
                runner.stop()
            else:
                print(f"{Fore.YELLOW}Não estava a correr nada.{Style.RESET_ALL}")
        elif action.kind == "toggle" and action.name:
            _toggle(runner, action.name, action.loop)
        elif action.kind == "new":
            _new_macro(runner)
        elif action.kind == "edit" and action.name:
            _edit_macro(runner, action.name)
        elif action.kind == "delete" and action.name:
            _delete_macro(runner, action.name)
        elif action.kind == "web":
            if web_url:
                print(f"{Fore.CYAN}{web_url}{Style.RESET_ALL}")
                webbrowser.open(web_url)
            else:
                print(f"{Fore.YELLOW}A pagina esta desligada (--no-web).{Style.RESET_ALL}")


# ─────────────────────────────────────────────────────────────────────────────
# Arranque
# ─────────────────────────────────────────────────────────────────────────────


def _on_finish(name: str, reason: str) -> None:
    colour = Fore.RED if reason.startswith("erro") else Fore.YELLOW
    print(f"\n{colour}■ {name}: {reason}{Style.RESET_ALL}")


def main(
    port: str | None = None,
    web_port: int = macro_server.DEFAULT_PORT,
    web: bool = True,
) -> None:
    port = port or settings.COM_PORT
    controller.connect(port)
    print(f"{Fore.GREEN}Arduino em {port}{Style.RESET_ALL}")

    xbox_passthrough.run(arduino_port=None)
    print(
        f"{Fore.GREEN}Comando ligado — a captura fica para o Elgato Studio"
        f"{Style.RESET_ALL}"
    )

    runner = MacroRunner()
    runner.on_finish = _on_finish

    server = None
    web_url = None
    if web:
        try:
            server = macro_server.MacroServer(runner, port=web_port)
            web_url = server.start()
            print(f"{Fore.GREEN}Página em {web_url}{Style.RESET_ALL}  ('w' abre)")
        except OSError as exc:
            # Porta ocupada não é razão para ficar sem macros no terminal.
            print(f"{Fore.YELLOW}Sem página web ({exc}){Style.RESET_ALL}")
            server = None

    try:
        _menu(runner, web_url)
    finally:
        runner.stop()
        if server is not None:
            server.stop()
        xbox_passthrough.stop()
        print(f"{Fore.GREEN}Até à próxima.{Style.RESET_ALL}")
        os._exit(0)


def _cli(argv: list[str]) -> None:
    """python shortcuts.py [COM8] [--port 8778] [--no-web]"""
    com_port, web_port, web = None, macro_server.DEFAULT_PORT, True
    rest = list(argv)
    while rest:
        argument = rest.pop(0)
        if argument == "--no-web":
            web = False
        elif argument == "--port":
            web_port = int(rest.pop(0))
        elif argument.startswith("-"):
            raise SystemExit(f"nao conheco {argument}\n{_cli.__doc__}")
        else:
            com_port = argument
    main(com_port, web_port=web_port, web=web)


if __name__ == "__main__":
    _cli(sys.argv[1:])
