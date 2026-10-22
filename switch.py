"""
switch.py — Project-wide bootstrap for switch-arduino scripts.

Import this once at the top of any script to get all utilities, colorised
output, and a one-call launch() that wires everything together.

Typical script layout
---------------------
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from switch import *

    _DEBUG_REGIONS = [("label", x1, y1, x2, y2, bgr_colour), ...]

    def main():
        click("A")
        ...

    if __name__ == "__main__":
        launch(main, debug_regions=_DEBUG_REGIONS)

Available after importing
-------------------------
Modules  : capture, controller, ocr, settings
Telegram : TelegramNotifier, TelegramAction, NotificationResult
Shortcuts: click, hold, hold_for, release, connect,
           set_left_stick, set_right_stick
Colorama : Fore, Style
Helpers  : start(debug_regions, key_callback, port)
           run(fn)
           launch(fn, debug_regions, key_callback, port)
"""

from __future__ import annotations

import os
import sys
import threading
import traceback

# ── Ensure utils/ is importable regardless of CWD or script location ──────────
_ROOT = os.path.dirname(os.path.abspath(__file__))
_UTILS = os.path.join(_ROOT, "utils")
for _p in (_ROOT, _UTILS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── Utils modules ─────────────────────────────────────────────────────────────
import capture  # noqa: E402
import controller  # noqa: E402
import ocr  # noqa: E402
import settings  # noqa: E402
import template_matcher
import xbox_passthrough  # noqa: E402
import builtins

# ── Colorama (initialise once for the whole process) ─────────────────────────
from colorama import Fore, Style  # noqa: E402
from colorama import init as _colorama_init

# ── Controller shortcuts ───────────────────────────────────────────────────────
from controller import release  # noqa: E402
from controller import click, connect, hold, hold_for, set_left_stick, set_right_stick
from recorder import end_recording, play_recording, start_recording, toggle_recording
from telegram_notifier import NotificationResult  # noqa: E402
from telegram_notifier import TelegramAction, TelegramNotifier
import pokemon_sv
from pokemon_sv import Mode, MenuOption

_colorama_init()

# ── Default notifier (shared across all scripts) ──────────────────────────────
notifier = TelegramNotifier(
    bot_token="8270624295:AAF6vixgzXNJq6FV-7jsOiDcewm1V2POgUQ",
    chat_id="1789658618",
    receive=settings.TELEGRAM_RECEIVE,
)

lock = threading.Lock()

# ── Public API ────────────────────────────────────────────────────────────────
__all__ = [
    # modules
    "capture",
    "controller",
    "ocr",
    "settings",
    "template_matcher",
    "pokemon_sv",
    # telegram
    "TelegramNotifier",
    "TelegramAction",
    "NotificationResult",
    "notifier",
    # controller shortcuts
    "click",
    "connect",
    "hold",
    "hold_for",
    "release",
    "set_left_stick",
    "set_right_stick",
    # colorama
    "Fore",
    "Style",
    # bootstrap helpers
    "launch",
    # recording
    "start_recording",
    "end_recording",
    "toggle_recording",
    "play_recording",
    # pokemon_sv
    "Mode",
    "MenuOption",
    # Aux
    "restart_sv",
]


# ─────────────────────────────────────────────────────────────────────────────
# Bootstrap helpers
# ─────────────────────────────────────────────────────────────────────────────


def _play(port: str | None = None) -> None:
    """Connect to the Arduino and open the Xbox controller passthrough window.

    Blocks until the window is closed.

    Parameters
    ----------
    port:
        Serial port override.  Defaults to ``settings.COM_PORT``.
    """
    xbox_passthrough.run(arduino_port=port or settings.COM_PORT)


def _cmd_screenshot(_: str) -> None:
    """Handler for the /screenshot bot command."""
    try:
        frame = capture.get_latest_frame()
    except:
        notifier.send_message("No frame available yet.", closer=True)
        return

    notifier.send_photo(frame=frame, caption="Screenshot", closer=True)


def thread_click(key, sleep=0.5, repeat=1):
    with lock:
        click(key, sleep, repeat)


def _start(
    debug_regions: list | None = None,
    key_callback=None,
    port: str | None = None,
    focus: bool = False,
) -> None:
    """Connect to the Arduino and open the capture window.

    Blocks until the capture window reports it is ready.

    Parameters
    ----------
    debug_regions:
        Overlay regions drawn on the preview window.
        Each entry: (label, x1, y1, x2, y2, bgr_colour).
    key_callback:
        Called for unhandled key presses: ``key_callback(key, frame)``.
    port:
        Serial port override.  Defaults to ``settings.COM_PORT``.
    focus:
        Whether the capture window keeps the focus when it opens.
        Defaults to False — the focus goes straight back to the window that
        had it (editor/terminal), and the capture window opens on another
        monitor so it can be watched while working.
    """
    controller.connect(port or settings.COM_PORT)
    capture.start_capture(
        press_callback=thread_click,
        debug_regions=debug_regions or [],
        key_callback=key_callback,
        focus=focus,
    )
    print(f"{Fore.GREEN}Connected to Arduino on {settings.COM_PORT}{Style.RESET_ALL}")
    capture.wait_ready()
    print(f"{Fore.GREEN}Capture window ready{Style.RESET_ALL}")
    capture.register_window_toggle_callback(xbox_passthrough.toggle_window)
    print(f"{Fore.GREEN}Registered window toggle callback{Style.RESET_ALL}")
    # Run the notifier command listener in a separate thread so it doesn't block the main thread
    notifier_thread = threading.Thread(target=run_notifier)
    notifier_thread.start()
    
    print(f"{Fore.GREEN}Registered command handlers{Style.RESET_ALL}")

    _play(port)

    print(f"{Fore.GREEN}Done{Style.RESET_ALL}")

def run_notifier():
    notifier.register_command("screenshot", _cmd_screenshot)
    notifier.register_command("resume", controller.resume)
    notifier.register_command("pause", controller.pause)
    notifier.register_command("toggle", controller.toggle_pause)
    notifier.start_command_listener()
def _run(fn) -> None:
    fn()


launched = False


def launch(
    fn,
    debug_regions: list | None = None,
    key_callback=None,
    port: str | None = None,
    focus: bool = False,
) -> None:
    global launched
    if launched:
        capture.stop()
        xbox_passthrough.stop()
        notifier.stop()
    launched = True
    """``start()`` followed by ``run(fn)`` — covers the full boilerplate block.

    Parameters
    ----------
    fn:
        The script's main function.
    debug_regions:
        Forwarded to ``start()``.
    key_callback:
        Forwarded to ``start()``.
    port:
        Serial port override.  Defaults to ``settings.COM_PORT``.
    focus:
        Whether the capture window keeps the focus when it opens (default
        False).  Forwarded to ``start()``.
    """
    try:
        _start(
            debug_regions=debug_regions,
            key_callback=key_callback,
            port=port,
            focus=focus,
        )
        _run(fn)
    except (KeyboardInterrupt, SystemExit):
        print("A")
        pass
    except Exception:
        traceback.print_exc()
    finally:
        print("B")
        os._exit(0)
        for _fn in (capture.stop, xbox_passthrough.stop, notifier.stop):
            try:
                _fn()
            except Exception:
                pass


def restart_sv():
    click("H",2)
    click("Y",2)
    click("A")
    capture.wait_for_pixel(1674, 2114, "#2f2d2f")
    click("A", 19)
    click("A", 13)


def better_input(prompt: object = "", /):
    try:
        return original_input(prompt)
    except (KeyboardInterrupt, EOFError):
        raise KeyboardInterrupt()


original_input = input

builtins.input = better_input
