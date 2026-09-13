"""
macro_runner.py — where macros are kept and what runs them.

The terminal menu and the web page are two faces of the same runner: whichever
one starts a macro, the other sees it running and can stop it.

Two rules hold the whole thing together:

  * **One macro at a time.**  The pad is muted while one runs, so a second one
    would be two hands on the same serial line.
  * **Nothing is left pressed.**  However a macro ends — finished, stopped,
    or blown up — every key it was holding is released, both sticks go back to
    centre, and the pad gets its voice back.

That second rule is the whole reason this is not a for-loop over `click()`:
with no capture card to look at (Elgato Studio has it while you play), a
button left stuck down is invisible until the game starts misbehaving.
"""

from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Protocol

from macros import Command, MacroError, parse

_DEFAULT_DIR = Path(__file__).resolve().parent.parent / "saved_outputs" / "macros"

# Macro names double as file names and as URL path segments, so they are kept
# to something boring on purpose.
_SAFE_NAME = re.compile(r"^[\w -]{1,64}$", re.UNICODE)

EXTENSION = ".macro"


# ─────────────────────────────────────────────────────────────────────────────
# Storage
# ─────────────────────────────────────────────────────────────────────────────


class MacroStore:
    """The macros on disk, one text file each."""

    def __init__(self, directory: os.PathLike | str | None = None):
        self.directory = Path(directory) if directory else _DEFAULT_DIR
        self.directory.mkdir(parents=True, exist_ok=True)

    def path_for(self, name: str) -> Path:
        """Return the file a macro lives in, refusing anything but a plain name."""
        cleaned = name.strip()
        if not _SAFE_NAME.match(cleaned):
            raise ValueError(
                f"nome invalido {name!r}: letras, numeros, espaco, - e _, ate 64"
            )
        return self.directory / f"{cleaned}{EXTENSION}"

    def names(self) -> list[str]:
        return sorted(p.stem for p in self.directory.glob(f"*{EXTENSION}"))

    def exists(self, name: str) -> bool:
        return self.path_for(name).exists()

    def load(self, name: str) -> str:
        return self.path_for(name).read_text(encoding="utf-8")

    def commands(self, name: str) -> list[Command]:
        """Parse a stored macro.  Raises MacroError if it no longer parses."""
        return parse(self.load(name))

    def save(self, name: str, text: str) -> Path:
        """Write a macro, but only once it parses — never store something broken."""
        path = self.path_for(name)
        parse(text)
        path.write_text(text, encoding="utf-8")
        return path

    def delete(self, name: str) -> None:
        self.path_for(name).unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# The two things a runner talks to
# ─────────────────────────────────────────────────────────────────────────────


class _IO(Protocol):
    """The slice of controller.py a macro needs."""

    def hold(self, key: str) -> Any: ...
    def release(self, key: str) -> Any: ...
    def set_left_stick(self, x: float, y: float) -> Any: ...
    def set_right_stick(self, x: float, y: float) -> Any: ...


class _Pad(Protocol):
    """The slice of xbox_passthrough.py a macro needs."""

    def suspend(self) -> None: ...
    def resume(self) -> None: ...


# ─────────────────────────────────────────────────────────────────────────────
# Running
# ─────────────────────────────────────────────────────────────────────────────


class MacroRunner:
    """Runs one macro at a time, in a thread, interruptibly."""

    def __init__(
        self,
        store: MacroStore | None = None,
        io: _IO | None = None,
        pad: _Pad | None = None,
    ):
        self.store = store or MacroStore()
        if io is None:
            import controller  # deferred: importing costs a serial library

            io = controller
        if pad is None:
            import xbox_passthrough

            pad = xbox_passthrough
        self._io = io
        self._pad = pad

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._state: dict[str, Any] | None = None
        # Keys this macro pressed and has not released yet.
        self._held: list[str] = []
        self._sticks_moved = False
        self.on_finish = None  # optional callback(name, reason)

    # ── state ────────────────────────────────────────────────────────────────

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def status(self) -> dict[str, Any] | None:
        """What is running right now, or None."""
        with self._lock:
            return dict(self._state) if self._state else None

    # ── control ──────────────────────────────────────────────────────────────

    def start(self, name: str, loop: bool = False) -> None:
        """Run a macro, replacing whatever was running.

        Parses first: a macro that does not parse never gets as far as muting
        the pad, so a typo costs you nothing.
        """
        commands = self.store.commands(name)  # raises MacroError before anything else

        self.stop()

        self._stop.clear()
        with self._lock:
            self._state = {
                "name": name,
                "loop": loop,
                "lap": 1,
                "started_at": time.time(),
            }
        self._pad.suspend()
        self._thread = threading.Thread(
            target=self._run,
            args=(name, commands, loop),
            daemon=True,
            name=f"macro-{name}",
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop whatever is running and wait for it to have tidied up."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
        self._thread = None

    def toggle(self, name: str, loop: bool = False) -> bool:
        """Start `name`, or stop it if it is the one already running.

        Returns True when it started, False when it stopped.
        """
        current = self.status()
        if current and current["name"] == name:
            self.stop()
            return False
        self.start(name, loop=loop)
        return True

    # ── the thread ───────────────────────────────────────────────────────────

    def _run(self, name: str, commands: list[Command], loop: bool) -> None:
        reason = "fim"
        try:
            lap = 1
            while not self._stop.is_set():
                for command in commands:
                    if self._stop.is_set():
                        break
                    self._execute(command)
                if not loop:
                    break
                lap += 1
                with self._lock:
                    if self._state:
                        self._state["lap"] = lap
            if self._stop.is_set():
                reason = "parada"
        except MacroError as exc:
            reason = f"erro: {exc}"
        except Exception as exc:  # noqa: BLE001 — a macro must never take the app with it
            reason = f"erro: {exc}"
        finally:
            self._cleanup()
            with self._lock:
                self._state = None
            if self.on_finish:
                try:
                    self.on_finish(name, reason)
                except Exception:
                    pass

    def _execute(self, command: Command) -> None:
        op, args = command.op, command.args

        if op == "click":
            for _ in range(args["repeat"]):
                if self._stop.is_set():
                    return
                self._hold(args["key"])
                self._sleep(args["hold"])
                self._release(args["key"])
                self._sleep(args["sleep"])
        elif op == "hold":
            self._hold(args["key"])
        elif op == "release":
            self._release(args["key"])
        elif op == "hold_for":
            self._hold(args["key"])
            self._sleep(args["duration"])
            self._release(args["key"])
        elif op == "wait":
            self._sleep(args["seconds"])
        elif op == "stick":
            self._stick(args["side"], args["x"], args["y"])

    # ── primitives ───────────────────────────────────────────────────────────

    def _hold(self, key: str) -> None:
        self._io.hold(key)
        if key not in self._held:
            self._held.append(key)

    def _release(self, key: str) -> None:
        self._io.release(key)
        if key in self._held:
            self._held.remove(key)

    def _stick(self, side: str, x: float, y: float) -> None:
        if side == "left":
            self._io.set_left_stick(x, y)
        else:
            self._io.set_right_stick(x, y)
        self._sticks_moved = self._sticks_moved or (x, y) != (0.0, 0.0)

    def _sleep(self, seconds: float) -> None:
        """Sleep, but wake immediately if the macro is told to stop."""
        if seconds > 0:
            self._stop.wait(seconds)

    def _cleanup(self) -> None:
        """Let go of everything, then give the pad its voice back."""
        for key in list(self._held):
            try:
                self._io.release(key)
            except Exception:
                pass
        self._held.clear()

        if self._sticks_moved:
            for centre in (self._io.set_left_stick, self._io.set_right_stick):
                try:
                    centre(0.0, 0.0)
                except Exception:
                    pass
            self._sticks_moved = False

        try:
            self._pad.resume()
        except Exception:
            pass
