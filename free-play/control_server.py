"""
free-play/control_server.py — canal HTTP local para conduzir a sessão de fora.

Só um processo pode ter o Elgato aberto, por isso não dá para abrir um segundo
script para ir buscar frames: o canal tem de viver dentro do processo que já
tem a janela de captura.  Este módulo levanta um servidor HTTP em
127.0.0.1:8777 que serve duas coisas:

  * comandos (screenshot, botões, sticks) para quem estiver de fora — ex.:
        curl "http://127.0.0.1:8777/shot"
        curl "http://127.0.0.1:8777/press?keys=A,4&sleep=0.4"
  * uma página (a "aba") com a última screenshot tirada e o último recorte
    selecionado na janela de captura, com auto-refresh.

Cada comando recebido é impresso na consola, para quem está a jogar ver o que
lhe está a mexer no jogo.
"""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import capture
import controller
import cv2
from colorama import Fore, Style

HOST = "127.0.0.1"
DEFAULT_PORT = 8777

_DIR = os.path.dirname(os.path.abspath(__file__))
SHOTS_DIR = os.path.join(_DIR, "shots")
_LATEST_PNG = os.path.join(SHOTS_DIR, "latest.png")
_ZONE_PNG = os.path.join(SHOTS_DIR, "zone.png")
_TAB_HTML = os.path.join(_DIR, "tab.html")

# controller._write has no lock and the passthrough thread writes to the same
# serial line, so at least keep our own commands from interleaving.
_cmd_lock = threading.Lock()

_state_lock = threading.Lock()
_shot = {"seq": 0, "name": None, "size": None, "zone": None, "at": None}
_zone = {"seq": 0, "coords": None, "size": None, "at": None}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _echo(what: str) -> None:
    print(f"{Fore.BLUE}claude:{Style.RESET_ALL} {what}")


def _write_png(path: str, img) -> None:
    """Write atomically — the browser tab may be reading the file right now."""
    tmp = path + ".tmp.png"
    cv2.imwrite(tmp, img)
    os.replace(tmp, path)


def _parse_zone(raw: str | None):
    """Turn a ?zone= value into 4K coords, or None for the full frame."""
    if not raw:
        return None
    if raw == "last":
        coords, _ = capture.get_last_selection()
        if coords is None:
            raise ValueError(
                "no zone selected yet (right-click, then click, on the capture window)"
            )
        return coords
    parts = [p for p in raw.replace(" ", "").split(",") if p]
    if len(parts) != 4:
        raise ValueError(f"zone needs 4 numbers or 'last', got {raw!r}")
    return tuple(int(p) for p in parts)


def take_shot(zone=None, keep=True) -> dict:
    """Grab the latest frame (optionally just a zone) and save it for the tab.

    With keep=False only latest.png is written — for a script that shoots in a
    loop and would otherwise bury shots/ in thousands of timestamped files.
    """
    frame = capture.get_latest_frame()
    if zone is not None:
        x1, y1, x2, y2 = zone
        frame = capture.get_frame_region(x1, y1, x2, y2, frame=frame)
        if frame.size == 0:
            raise ValueError(f"zone {zone} is empty at this resolution")
    os.makedirs(SHOTS_DIR, exist_ok=True)
    name = f"{time.strftime('%Y%m%d-%H%M%S')}.png" if keep else "latest.png"
    if keep:
        _write_png(os.path.join(SHOTS_DIR, name), frame)
    _write_png(_LATEST_PNG, frame)
    h, w = frame.shape[:2]
    with _state_lock:
        _shot.update(
            seq=_shot["seq"] + 1,
            name=name,
            size=[w, h],
            zone=list(zone) if zone else None,
            at=time.strftime("%H:%M:%S"),
        )
        return dict(_shot)


def grab_zone() -> dict:
    """Save the crop the user last selected on the capture window."""
    coords, crop = capture.get_last_selection()
    if coords is None or crop is None:
        raise ValueError(
            "no zone selected yet (right-click, then click, on the capture window)"
        )
    os.makedirs(SHOTS_DIR, exist_ok=True)
    _write_png(_ZONE_PNG, crop)
    h, w = crop.shape[:2]
    with _state_lock:
        _zone.update(
            seq=_zone["seq"] + 1,
            coords=list(coords),
            size=[w, h],
            at=time.strftime("%H:%M:%S"),
        )
        return dict(_zone)


# ─────────────────────────────────────────────────────────────────────────────
# Commands
# ─────────────────────────────────────────────────────────────────────────────


def press(keys: list[str], sleep: float, repeat: int) -> None:
    with _cmd_lock:
        for _ in range(repeat):
            for key in keys:
                controller.click(key, sleep=sleep)


def hold(key: str, secs: float) -> None:
    with _cmd_lock:
        controller.hold_for(key, secs)


def stick(side: str, x: float, y: float, secs: float) -> None:
    setter = controller.set_left_stick if side == "left" else controller.set_right_stick
    with _cmd_lock:
        setter(x, y)
        time.sleep(secs)
        setter(0.0, 0.0)


# ─────────────────────────────────────────────────────────────────────────────
# The page (tab.html) and the usage text
# ─────────────────────────────────────────────────────────────────────────────

_USAGE = """free-play control server

  GET /                                    a aba com a ultima screenshot
  GET /shot[?zone=…][&keep=0]              tira screenshot (keep=0 nao arquiva)
  GET /zone                                guarda o ultimo recorte selecionado
  GET /press?keys=A,4&sleep=0.4&repeat=1   sequencia de botoes
  GET /hold?key=w&secs=0.6                 segura um botao
  GET /stick?side=left&x=0&y=-1&secs=0.3   mexe o stick e recentra
  GET /state                               estado do servidor

Botoes: A B X Y C H + - L R l r | d-pad 1 2 3 4
Sticks: w a s d q e z c (esq)   u h j k y i n m (dir)
"""


# ─────────────────────────────────────────────────────────────────────────────
# HTTP
# ─────────────────────────────────────────────────────────────────────────────


class _Handler(BaseHTTPRequestHandler):
    server_version = "free-play/1.0"

    def log_message(self, format: str, *args) -> None:  # keep the console clean
        pass

    # -- replies ---------------------------------------------------------
    def _send(self, body: bytes, ctype: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _text(self, text: str, code: int = 200) -> None:
        self._send(text.encode("utf-8"), "text/plain; charset=utf-8", code)

    def _file(self, path: str) -> None:
        try:
            with open(path, "rb") as fh:
                self._send(fh.read(), "image/png")
        except OSError:
            self._text("no image yet\n", 404)

    # -- routing ---------------------------------------------------------
    def do_GET(self) -> None:
        url = urlparse(self.path)
        q = {k: v[-1] for k, v in parse_qs(url.query).items()}
        try:
            self._route(url.path, q)
        except Exception as exc:
            _echo(f"{Fore.RED}erro{Style.RESET_ALL} {url.path} — {exc}")
            self._text(f"error: {exc}\n", 400)

    def _route(self, path: str, q: dict) -> None:
        if path == "/":
            # Read from disk every request — tweak tab.html, hit refresh, no
            # restart (restarting would drop the capture window mid-session).
            with open(_TAB_HTML, "rb") as fh:
                self._send(fh.read(), "text/html; charset=utf-8")

        elif path == "/shot.png":
            self._file(_LATEST_PNG)

        elif path == "/zone.png":
            self._file(_ZONE_PNG)

        elif path == "/status.json":
            with _state_lock:
                body = json.dumps({"shot": dict(_shot), "zone": dict(_zone)})
            self._send(body.encode("utf-8"), "application/json")

        elif path == "/shot":
            zone = _parse_zone(q.get("zone"))
            keep = q.get("keep", "1") not in ("0", "false", "no")
            st = take_shot(zone, keep=keep)
            where = f" zona {st['zone']}" if st["zone"] else ""
            _echo(f"screenshot{where} -> shots/{st['name']}")
            self._text(
                f"free-play/shots/{st['name']} "
                f"{st['size'][0]}x{st['size'][1]}{where}\n"
            )

        elif path == "/zone":
            st = grab_zone()
            _echo(f"leu a zona {st['coords']} -> shots/zone.png")
            self._text(
                f"free-play/shots/zone.png {st['size'][0]}x{st['size'][1]} "
                f"zona {', '.join(str(c) for c in st['coords'])}\n"
            )

        elif path == "/press":
            keys = [k for k in q.get("keys", "").replace(" ", "").split(",") if k]
            if not keys:
                raise ValueError("keys is required, e.g. /press?keys=A,4")
            sleep = float(q.get("sleep", 0.4))
            repeat = int(q.get("repeat", 1))
            _echo(
                f"press {','.join(keys)}"
                + (f" x{repeat}" if repeat > 1 else "")
                + f" (sleep {sleep}s)"
            )
            press(keys, sleep, repeat)
            self._text("ok\n")

        elif path == "/hold":
            key = q.get("key", "")
            if not key:
                raise ValueError("key is required, e.g. /hold?key=w&secs=0.6")
            secs = float(q.get("secs", 0.5))
            _echo(f"hold {key} {secs}s")
            hold(key, secs)
            self._text("ok\n")

        elif path == "/stick":
            side = q.get("side", "left")
            if side not in ("left", "right"):
                raise ValueError("side must be left or right")
            x, y = float(q.get("x", 0.0)), float(q.get("y", 0.0))
            secs = float(q.get("secs", 0.3))
            _echo(f"stick {side} ({x}, {y}) {secs}s")
            stick(side, x, y, secs)
            self._text("ok\n")

        elif path == "/state":
            try:
                frame = capture.get_latest_frame()
                frame_txt = f"{frame.shape[1]}x{frame.shape[0]}"
            except Exception as exc:
                frame_txt = f"none ({exc})"
            coords, _ = capture.get_last_selection()
            with _state_lock:
                shot_txt = _shot["name"] or "none"
                shots = _shot["seq"]
            self._text(
                f"frame: {frame_txt}\n"
                f"capture stopped: {capture.is_stopped()}\n"
                f"controller paused: {controller.is_paused()}\n"
                f"shots taken: {shots} (last: {shot_txt})\n"
                f"last zone: {coords if coords else 'none'}\n"
            )

        else:
            self._text(_USAGE, 404)


_server: ThreadingHTTPServer | None = None


def start(port: int = DEFAULT_PORT) -> str:
    """Start the control server in a daemon thread and return its URL."""
    global _server
    _server = ThreadingHTTPServer((HOST, port), _Handler)
    _server.daemon_threads = True
    threading.Thread(target=_server.serve_forever, daemon=True).start()
    return f"http://{HOST}:{port}/"


def stop() -> None:
    if _server is not None:
        _server.shutdown()
