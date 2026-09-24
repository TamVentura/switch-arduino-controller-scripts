"""
xbox_passthrough.py — Forward Xbox controller input to the Nintendo Switch via Arduino.

Opens a small GUI window to select which connected Xbox/XInput controller should
control the Switch.  Handles hot-plug (connection and disconnection) automatically.

Usage — standalone:
    python utils/xbox_passthrough.py          # uses settings.COM_PORT
    python utils/xbox_passthrough.py COM8     # override port

Usage — embedded (call after controller.connect()):
    from utils.xbox_passthrough import run
    run()   # blocks until the window is closed
"""

from __future__ import annotations

import os
import sys
import threading
import time
from ctypes import windll, wintypes
from tkinter import font as tkfont
from typing import Tuple

import pygame
from mttkinter import mtTkinter as tk

# Allow sibling-module imports regardless of CWD / how this file is invoked
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import controller as sw
import settings

# ─────────────────────────────────────────────────────────────────────────────
# Input mapping  (XInput / Xbox controller on Windows via pygame)
# ─────────────────────────────────────────────────────────────────────────────

# pygame button index → Arduino command character
# Adjust indices here if your driver reports a different order.
BUTTON_MAP: dict[int, str] = {
    0: "B",  # A
    1: "A",  # B
    2: "Y",  # X
    3: "X",  # Y
    4: "L",  # Left Bumper  → L
    5: "R",  # Right Bumper → R
    6: "-",  # Back / View  → Minus
    7: "+",  # Start / Menu → Plus
    8: "{",  # Left-stick click  → LCLICK
    9: "}",  # Right-stick click → RCLICK
    10: "H",  # Guide / Home
}

# pygame axis indices (XInput order on Windows)
AXIS_LEFT_X = 0
AXIS_LEFT_Y = 1
AXIS_RIGHT_X = 2  # Left Trigger:  –1.0 released → +1.0 fully pressed
AXIS_RIGHT_Y = 3  # Right Trigger: –1.0 released → +1.0 fully pressed
AXIS_LT = 4
AXIS_RT = 5

HAT_DPAD = 0  # pygame hat index for the D-pad

# Trigger is "pressed" when axis value exceeds this threshold
# (triggers idle at –1.0, so 0.0 ≈ half press)
TRIGGER_THRESHOLD: float = 0.0

# Stick values smaller than this magnitude are snapped to zero
STICK_DEADZONE: float = 0.10

# Polling rate for the input thread
POLL_HZ: int = 60

# Buttons that form the pause chord (Select=6, Start=7)
COMBO_BUTTONS: frozenset[int] = frozenset({})  # frozenset({6, 7})
# How long to wait for the second button before forwarding the first (seconds)
COMBO_WINDOW_S: float = 0.15


# ─────────────────────────────────────────────────────────────────────────────
# D-pad helpers
# ─────────────────────────────────────────────────────────────────────────────


def _hat_cmds(hat: Tuple[float, float]) -> list[str]:
    """Return Arduino command chars for a given D-pad hat value (x, y)."""
    x, y = hat
    cmds: list[str] = []
    if y == 1:
        cmds.append("4")  # Up
    if y == -1:
        cmds.append("2")  # Down
    if x == -1:
        cmds.append("3")  # Left
    if x == 1:
        cmds.append("1")  # Right
    return cmds


def _deadzone(v: float) -> float:
    return 0.0 if abs(v) < STICK_DEADZONE else v


# ─────────────────────────────────────────────────────────────────────────────
# Shared state (written by GUI thread, read by input thread)
# ─────────────────────────────────────────────────────────────────────────────

_lock = threading.Lock()
_selected_instance_id: int | None = 1  # stable pygame instance_id, or None
_shutdown = threading.Event()
# While set, the pad is muted: nothing it does reaches the Switch.  Used to get
# the pad out of the way of a running macro -- both write to the same serial
# line, so a macro only does exactly what it says if the pad stays quiet.
_suspended = threading.Event()
_root: "tk.Tk | None" = None
_window_visible: bool = False  # tracks whether the window is currently shown


def _set_selected(iid: int | None) -> None:
    global _selected_instance_id
    with _lock:
        _selected_instance_id = iid


def _get_selected() -> int | None:
    with _lock:
        return _selected_instance_id


def suspend() -> None:
    """Mute the pad: it stops reaching the Switch until resume() is called.

    Everything it was holding is released on the next tick, so nothing stays
    pressed while a macro is running.
    """
    _suspended.set()


def resume() -> None:
    """Un-mute the pad, resending whatever is still being held or pushed."""
    _suspended.clear()


def is_suspended() -> bool:
    return _suspended.is_set()


# ─────────────────────────────────────────────────────────────────────────────
# Input polling thread
# ─────────────────────────────────────────────────────────────────────────────


class _InputThread(threading.Thread):
    """Reads the selected joystick at POLL_HZ and forwards inputs to the Arduino."""

    def __init__(self, on_disconnect: "callable[[int], None]"):  # type: ignore
        super().__init__(daemon=True, name="xbox-input")
        self._on_disconnect = on_disconnect
        self._joy: pygame.joystick.JoystickType | None = None
        self._iid: int | None = None
        # Previous state — used to detect changes
        self._buttons: dict[int, bool] = {}
        self._hat: Tuple[float, float] = (0, 0)
        self._lt_held = False
        self._rt_held = False
        self._previous_axix_l = (0, 0)
        self._previous_axix_r = (0, 0)
        # True once the pad has been let go of because of a suspension.
        self._muted = False
        # Pause-chord state: "pending" (held, not yet forwarded) |
        #                    "sent" (forwarded normally) |
        #                    "suppressed" (chord fired, do not forward)
        self._combo_state: dict[int, str] = {}
        self._combo_press_time: dict[int, float] = {}

    # ── state helpers ─────────────────────────────────────────────────────────

    def _release_all(self) -> None:
        """Release every held input (called when switching/losing controller)."""
        for idx, held in self._buttons.items():
            if held:
                if self._combo_state.get(idx) in ("pending", "suppressed"):
                    continue  # never forwarded, no release needed
                cmd = BUTTON_MAP.get(idx)
                if cmd:
                    try:
                        sw.release(cmd)
                    except Exception:
                        pass
        for cmd in _hat_cmds(self._hat):
            try:
                sw.release(cmd)
            except Exception:
                pass
        if self._lt_held:
            try:
                sw.release("l")
            except Exception:
                pass
        if self._rt_held:
            try:
                sw.release("r")
            except Exception:
                pass
        try:
            sw.set_left_stick(0.0, 0.0)
            sw.set_right_stick(0.0, 0.0)
        except Exception:
            pass
        self._buttons = {}
        self._hat = (0, 0)
        self._lt_held = False
        self._rt_held = False
        # Forget the last stick positions too, otherwise a stick that is still
        # pushed over looks unchanged on the next poll and never gets resent --
        # the Switch would stay centred while the pad is not.
        self._previous_axix_l = (0, 0)
        self._previous_axix_r = (0, 0)
        self._combo_state.clear()
        self._combo_press_time.clear()

    def _find_joystick(self, iid: int) -> pygame.joystick.JoystickType | None:
        """Return an initialised Joystick whose instance_id matches iid."""
        for i in range(pygame.joystick.get_count()):
            try:
                j = pygame.joystick.Joystick(i)
                j.init()
                if j.get_instance_id() == iid:
                    return j
            except pygame.error:
                continue
        return None

    # ── main loop ─────────────────────────────────────────────────────────────

    def run(self) -> None:
        sw.mark_as_bypass()
        while not _shutdown.is_set():
            pygame.event.get()  # drain queue — required for get_button() to update on Windows
            self._tick()
            time.sleep(1.0 / POLL_HZ)

    def _tick(self) -> None:
        """One pass of the input loop."""
        if _suspended.is_set():
            if not self._muted:
                # Let go of everything once, then stay quiet: a macro is running
                # and the pad must not fight it on the serial line.
                self._release_all()
                self._muted = True
            return
        if self._muted:
            # Coming back.  _release_all() wiped the remembered state, so the
            # poll below resends whatever is still held or pushed.
            self._muted = False

        sel = _get_selected()

        # Selection changed — switch joystick objects
        if sel != self._iid:
            self._release_all()
            self._iid = sel
            if sel is not None:
                self._joy = self._find_joystick(sel)
                if self._joy is None:
                    # ID no longer valid
                    self._iid = None
                    _set_selected(None)
            else:
                self._joy = None
        if self._joy is None:
            return

        try:
            self._poll()
        except pygame.error:
            # Controller disconnected mid-poll
            lost_iid = self._iid
            self._release_all()
            self._joy = None
            self._iid = None
            _set_selected(None)
            if lost_iid is not None:
                self._on_disconnect(lost_iid)

    def _poll(self) -> None:
        joy = self._joy
        if joy == None:
            return
        n_axes = joy.get_numaxes()
        n_buttons = joy.get_numbuttons()
        n_hats = joy.get_numhats()

        # ── Buttons ────────────────────────────────────────────────────────────
        for idx in range(n_buttons):
            pressed = bool(joy.get_button(idx))
            was = self._buttons.get(idx, False)

            if idx in COMBO_BUTTONS:
                if pressed and not was:
                    # Defer: record press time, decide later whether to forward
                    self._combo_state[idx] = "pending"
                    self._combo_press_time[idx] = time.monotonic()
                elif not pressed and was:
                    state = self._combo_state.pop(idx, None)
                    self._combo_press_time.pop(idx, None)
                    if state == "sent":
                        cmd = BUTTON_MAP.get(idx)
                        if cmd:
                            sw.release(cmd)
                    elif state == "pending":
                        # Released before window expired — if the other combo button
                        # was never involved, treat it as a normal solo tap
                        other_active = any(
                            self._combo_state.get(b) in ("pending", "suppressed")
                            for b in COMBO_BUTTONS
                            if b != idx
                        )
                        if not other_active:
                            cmd = BUTTON_MAP.get(idx)
                            if cmd:
                                sw.click(cmd)
                    # suppressed: chord fired, discard silently
            else:
                if pressed != was:
                    cmd = BUTTON_MAP.get(idx)
                    if cmd:
                        sw.hold(cmd) if pressed else sw.release(cmd)

            self._buttons[idx] = pressed

        # Fire combo if both buttons are still pending (neither forwarded yet)
        # pending = {b for b, s in self._combo_state.items() if s == "pending"}
        # if COMBO_BUTTONS <= pending:
        #     for b in COMBO_BUTTONS:
        #         self._combo_state[b] = "suppressed"
        #         self._combo_press_time.pop(b, None)
        #     sw.toggle_pause()

        # # Flush any pending button whose window has expired without a combo
        # now = time.monotonic()
        # for idx in list(self._combo_press_time):
        #     if now - self._combo_press_time[idx] > COMBO_WINDOW_S:
        #         self._combo_press_time.pop(idx)
        #         if self._buttons.get(idx, False):
        #             cmd = BUTTON_MAP.get(idx)
        #             if cmd:
        #                 sw.hold(cmd)
        #             self._combo_state[idx] = "sent"
        #         else:
        #             self._combo_state.pop(idx, None)

        # ── Triggers (analog → digital ZL / ZR) ────────────────────────────────
        if n_axes > AXIS_LT:
            lt = joy.get_axis(AXIS_LT) > TRIGGER_THRESHOLD
            if lt != self._lt_held:
                sw.hold("l") if lt else sw.release("l")
                self._lt_held = lt
        if n_axes > AXIS_RT:
            rt = joy.get_axis(AXIS_RT) > TRIGGER_THRESHOLD
            if rt != self._rt_held:
                sw.hold("r") if rt else sw.release("r")
                self._rt_held = rt

        # ── D-pad ──────────────────────────────────────────────────────────────
        if n_hats > HAT_DPAD:
            hat: Tuple[float, float] = joy.get_hat(HAT_DPAD)
            if hat != self._hat:
                for cmd in _hat_cmds(self._hat):
                    sw.release(cmd)
                for cmd in _hat_cmds(hat):
                    sw.hold(cmd)
                self._hat = hat

        # ── Analog sticks ──────────────────────────────────────────────────────
        if n_axes > max(AXIS_LEFT_X, AXIS_LEFT_Y):

            if self._previous_axix_l != (
                joy.get_axis(AXIS_LEFT_X),
                joy.get_axis(AXIS_LEFT_Y),
            ):
                self._previous_axix_l = (
                    joy.get_axis(AXIS_LEFT_X),
                    joy.get_axis(AXIS_LEFT_Y),
                )
                sw.set_left_stick(
                    _deadzone(joy.get_axis(AXIS_LEFT_X)),
                    _deadzone(joy.get_axis(AXIS_LEFT_Y)),
                )
        if n_axes > max(AXIS_RIGHT_X, AXIS_RIGHT_Y):
            if self._previous_axix_r != (
                joy.get_axis(AXIS_RIGHT_X),
                joy.get_axis(AXIS_RIGHT_Y),
            ):
                self._previous_axix_r = (
                    joy.get_axis(AXIS_RIGHT_X),
                    joy.get_axis(AXIS_RIGHT_Y),
                )
                sw.set_right_stick(
                    _deadzone(joy.get_axis(AXIS_RIGHT_X)),
                    _deadzone(joy.get_axis(AXIS_RIGHT_Y)),
                )


# ─────────────────────────────────────────────────────────────────────────────
# GUI
# ─────────────────────────────────────────────────────────────────────────────


class ControllerSelectorWindow:
    """Tkinter window that lists available controllers and lets the user pick one."""

    _REFRESH_MS = 500  # how often to rescan for new/removed controllers

    def __init__(self, root: tk.Tk):
        self._root = root
        root.title("Nintendo Switch — Controller Passthrough")
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.bind("<v>", lambda _e: toggle_window())

        bold = tkfont.Font(weight="bold", size=10)

        # ── Header ────────────────────────────────────────────────────────────
        tk.Label(root, text="Select controller to use:", font=bold, pady=6).pack(
            fill=tk.X, padx=12
        )

        # ── Listbox ───────────────────────────────────────────────────────────
        frame = tk.Frame(root)
        frame.pack(fill=tk.BOTH, padx=12)

        scrollbar = tk.Scrollbar(frame, orient=tk.VERTICAL)
        self._listbox = tk.Listbox(
            frame,
            yscrollcommand=scrollbar.set,
            height=8,
            width=40,
            selectmode=tk.SINGLE,
            activestyle="none",
        )
        scrollbar.config(command=self._listbox.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._listbox.bind("<<ListboxSelect>>", self._on_select)

        # ── Status bar ────────────────────────────────────────────────────────
        self._status_var = tk.StringVar(value="Active: None")
        tk.Label(
            root, textvariable=self._status_var, anchor="w", fg="gray30", pady=4
        ).pack(fill=tk.X, padx=12)

        # Internal tracking
        # Maps listbox index → instance_id (or None for the "None" row)
        self._index_to_iid: list[int | None] = []
        self._last_count = -1

        # Populate immediately, then schedule periodic refresh
        self._refresh_controllers()

    # ── Controller list management ────────────────────────────────────────────

    def _get_controllers(self) -> list[tuple[int, str]]:
        """Return [(instance_id, label), …] for every connected joystick."""
        result: list[tuple[int, str]] = []

        for i in range(pygame.joystick.get_count()):
            try:
                j = pygame.joystick.Joystick(i)
                j.init()
                result.append((j.get_instance_id(), f"{j.get_name()}  (#{i})"))
            except pygame.error:
                continue
        return result

    def _refresh_controllers(self) -> None:
        controllers = self._get_controllers()
        # Only rebuild the list when the count changes (avoids flicker)
        if len(controllers) != self._last_count:
            self._last_count = len(controllers)
            sel_iid = _get_selected()

            self._listbox.delete(0, tk.END)
            self._index_to_iid = [None]
            self._listbox.insert(tk.END, "— None —")

            for iid, label in controllers:
                self._index_to_iid.append(iid)
                self._listbox.insert(tk.END, f"  {label}")

            # Restore selection highlight
            target_idx = 0
            if _get_selected() == None and len(self._index_to_iid) > 1:
                _set_selected(self._index_to_iid[1])
                sel_iid = self._index_to_iid[1]

            for idx, iid in enumerate(self._index_to_iid):
                if iid == sel_iid:
                    target_idx = idx
                    break
            self._listbox.selection_clear(0, tk.END)
            self._listbox.selection_set(target_idx)
            self._listbox.see(target_idx)
            self._on_select(tk.Event())

        self._root.after(self._REFRESH_MS, self._refresh_controllers)

    # ── Selection ─────────────────────────────────────────────────────────────

    def _on_select(self, _event: tk.Event) -> None:
        idxs = self._listbox.curselection()
        if not idxs:
            return
        idx = idxs[0]
        if idx >= len(self._index_to_iid):
            return
        iid = self._index_to_iid[idx]
        _set_selected(iid)

        if iid is None:
            self._status_var.set("Active: None")
        else:
            label = self._listbox.get(idx).strip()
            self._status_var.set(f"Active: {label}")

    def notify_disconnect(self, iid: int) -> None:
        """Called by the input thread when the active controller disconnects."""
        # Find its label in the list (may already be gone after rescan)
        label = f"instance #{iid}"
        for idx, stored_iid in enumerate(self._index_to_iid):
            if stored_iid == iid:
                label = self._listbox.get(idx).strip()
                break

        self._status_var.set(f"Disconnected: {label}")
        # Reset highlight to "None"
        self._listbox.selection_clear(0, tk.END)
        self._listbox.selection_set(0)
        self._last_count = -1  # force list rebuild on next refresh

    # ── Shutdown ──────────────────────────────────────────────────────────────

    def _on_close(self) -> None:
        _shutdown.set()
        self._root.destroy()


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────


def run(arduino_port: str | None = None) -> None:
    threading.Thread(target=_run, daemon=True, args=[arduino_port]).start()


def stop() -> None:
    _shutdown.set()
    if _root is not None:
        _root.after(0, _root.quit)


def toggle_window() -> None:
    """Show or hide the controller-selector window without stopping the mainloop.

    Uses withdraw() / deiconify() so the window disappears from the taskbar
    when hidden. The internal after()-based refresh loop keeps running either way.
    """
    global _window_visible
    r = _root
    if r is None:
        return

    def _do() -> None:
        global _window_visible
        if _window_visible:
            r.withdraw()
            _window_visible = False
        else:
            # Set WS_EX_NOACTIVATE before deiconify so the WM_ACTIVATE message
            # that tkinter sends is ignored by Windows — the capture window keeps
            # focus. Remove the style afterwards so the user can still click the
            # window to interact with it.
            hwnd = r.winfo_id()
            GWL_EXSTYLE = -20
            WS_EX_NOACTIVATE = 0x08000000
            prev = windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, prev | WS_EX_NOACTIVATE)
            r.deiconify()
            r.update_idletasks()  # flush WM_ACTIVATE while NOACTIVATE is still set
            windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, prev)  # restore
            _window_visible = True

    r.after(0, _do)


def _run(arduino_port: str | None = None) -> None:
    """
    Open the controller-selector window and start forwarding input to the Switch.

    Returns immediately — the GUI runs in its own background thread.

    Parameters
    ----------
    arduino_port:
        Serial port for the Arduino (e.g. "COM6").  Defaults to settings.COM_PORT.
        Pass None if controller.connect() was already called before this function.
    """
    global _selected_instance_id, _root, _window_visible
    _selected_instance_id = None
    _shutdown.clear()

    # Connect to Arduino if not already connected
    if arduino_port is not None:
        sw.connect(arduino_port)
    elif sw._arduino is None:
        sw.connect(settings.COM_PORT)

    # Initialise pygame joystick subsystem (no display window needed)
    pygame.init()
    pygame.joystick.init()
    _root = tk.Tk()
    _root.withdraw()      # hidden by default — no taskbar entry
    _window_visible = False
    window = ControllerSelectorWindow(_root)
    _root.update()

    def _on_disconnect(iid: int) -> None:
        r, w = _root, window
        if r is not None and w is not None:
            r.after(0, lambda: w.notify_disconnect(iid))

    input_thread = _InputThread(on_disconnect=_on_disconnect)
    input_thread.start()

    try:
        _root.mainloop()
    except Exception:
        _root.quit()
    finally:
        _root.quit()
        _shutdown.set()
        input_thread.join(timeout=0.0)
        pygame.quit()


# ─────────────────────────────────────────────────────────────────────────────
# Standalone entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = sys.argv[1] if len(sys.argv) > 1 else None
    _run(arduino_port=port)
