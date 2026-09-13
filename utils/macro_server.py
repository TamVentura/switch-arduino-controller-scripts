"""
macro_server.py — o canal web para as macros.

A página e o menu do terminal são duas mãos no mesmo MacroRunner: o que uma
arranca, a outra vê a correr e pode parar.  É o mesmo truque do
free-play/control_server.py, e pela mesma razão — o processo que tem o
Arduino é um só, por isso quem quiser mandar-lhe comandos de fora tem de
entrar por aqui.

    GET    /                          a página
    GET    /api/macros                nomes + o que está a correr
    GET    /api/macro/<nome>          o texto de uma macro
    PUT    /api/macro/<nome>          grava (corpo = o texto)
    DELETE /api/macro/<nome>          apaga
    POST   /api/macro/<nome>/toggle   arranca/pára  (?loop=1 em loop)
    POST   /api/stop                  pára o que estiver a correr

Também dá por curl, para disparar uma macro de fora sem abrir o browser:

    curl -X POST "http://127.0.0.1:8778/api/macro/passear/toggle?loop=1"
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from colorama import Fore, Style

from macro_runner import MacroRunner
from macros import MacroError

HOST = "127.0.0.1"
# 8777 é do free-play; este fica ao lado para poderem correr os dois.
DEFAULT_PORT = 8778

_PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "macro_tab.html")


def _echo(what: str) -> None:
    """Dizer na consola o que veio de fora — estás a jogar, deves saber."""
    print(f"\n{Fore.BLUE}web:{Style.RESET_ALL} {what}")


# ─────────────────────────────────────────────────────────────────────────────
# HTTP
# ─────────────────────────────────────────────────────────────────────────────


class _Handler(BaseHTTPRequestHandler):
    server_version = "switch-macros/1.0"
    runner: MacroRunner  # posto pelo MacroServer

    def log_message(self, format: str, *args) -> None:  # consola limpa
        pass

    # ── respostas ────────────────────────────────────────────────────────────

    def _send(self, body: bytes, ctype: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _text(self, text: str, code: int = 200) -> None:
        self._send(text.encode("utf-8"), "text/plain; charset=utf-8", code)

    def _json(self, payload, code: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(body, "application/json; charset=utf-8", code)

    # ── encaminhamento ───────────────────────────────────────────────────────

    def do_GET(self) -> None:
        self._handle("GET")

    def do_PUT(self) -> None:
        self._handle("PUT")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_DELETE(self) -> None:
        self._handle("DELETE")

    def _handle(self, method: str) -> None:
        url = urlparse(self.path)
        query = {k: v[-1] for k, v in parse_qs(url.query).items()}
        try:
            self._route(method, unquote(url.path), query)
        except MacroError as exc:
            self._text(str(exc), 400)
        except ValueError as exc:
            self._text(str(exc), 400)
        except FileNotFoundError:
            self._text("essa macro nao existe\n", 404)
        except Exception as exc:  # noqa: BLE001 — um pedido nunca leva o jogo atras
            _echo(f"{Fore.RED}erro{Style.RESET_ALL} {self.path} — {exc}")
            self._text(f"erro: {exc}\n", 500)

    def _body(self) -> str:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length).decode("utf-8") if length else ""

    def _route(self, method: str, path: str, query: dict) -> None:
        runner = self.runner

        if path == "/" and method == "GET":
            # Lido do disco a cada pedido: mexer no html e refrescar chega,
            # sem reiniciar (reiniciar perdia a ligação ao Arduino).
            with open(_PAGE, "rb") as fh:
                self._send(fh.read(), "text/html; charset=utf-8")
            return

        if path == "/api/macros" and method == "GET":
            self._json({"names": runner.store.names(), "status": runner.status()})
            return

        if path == "/api/stop" and method == "POST":
            if runner.is_running():
                _echo("parar")
                runner.stop()
            self._json({"status": runner.status()})
            return

        if path.startswith("/api/macro/"):
            rest = path[len("/api/macro/") :]

            if rest.endswith("/toggle") and method == "POST":
                name = rest[: -len("/toggle")]
                loop = query.get("loop", "0") not in ("0", "false", "no", "")
                self._toggle(name, loop)
                return

            name = rest
            if method == "GET":
                self._json({"name": name, "text": runner.store.load(name)})
                return
            if method == "PUT":
                runner.store.save(name, self._body())
                _echo(f"gravou {name}")
                self._json({"name": name, "ok": True})
                return
            if method == "DELETE":
                runner.store.delete(name)
                _echo(f"apagou {name}")
                self._json({"name": name, "ok": True})
                return

        self._text("nao ha nada aqui\n", 404)

    def _toggle(self, name: str, loop: bool) -> None:
        runner = self.runner
        current = runner.status()
        if current and current["name"] != name:
            _echo(f"a trocar {current['name']} por {name}")
        started = runner.toggle(name, loop=loop)
        if started:
            _echo(f"{name} a correr ({'loop' if loop else 'uma vez'})")
        self._json({"running": started, "status": runner.status()})


# ─────────────────────────────────────────────────────────────────────────────
# Servidor
# ─────────────────────────────────────────────────────────────────────────────


class MacroServer:
    """O servidor numa thread, para conviver com o menu no terminal."""

    def __init__(self, runner: MacroRunner, port: int = DEFAULT_PORT):
        self._runner = runner
        self._port = port
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        port = self._httpd.server_address[1] if self._httpd else self._port
        return f"http://{HOST}:{port}"

    def start(self) -> str:
        handler = type("_BoundHandler", (_Handler,), {"runner": self._runner})
        self._httpd = ThreadingHTTPServer((HOST, self._port), handler)
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            daemon=True,
            name="macro-server",
        )
        self._thread.start()
        return self.url

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
