"""
controller.py — Arduino serial pseudo-library.

Manages the serial connection to the Arduino and exposes press/hold helpers.

Usage
-----
    import controller
    controller.connect("COM6")   # or pass port=None to use the default
    controller.click("A", sleep=0.5, repeat=3)
    controller.hold_for("w", duration=1.2)
"""

import threading
import time
import typing

import serial
import settings
from colorama import Fore, Style
from globals import switch_gate

_arduino = None

# Last sent stick byte values (0–255, centre = 128).
_left_last_bx = -128
_left_last_by = -128
_right_last_bx = -128
_right_last_by = -128

# Minimum byte change (out of 255) required before resending a stick command.
_STICK_THRESHOLD = 1


_port = None
_baudrate = None
_timeout = None

# ---------------------------------------------------------------------------
# Pause gate — blocks script threads without affecting capture / xbox threads
# ---------------------------------------------------------------------------

# Per-thread bypass flag: threads that should never be blocked by the gate.
_tls = threading.local()


def mark_as_bypass() -> None:
    """Mark the CALLING thread as exempt from the pause gate.

    Call once at the start of any thread that should keep sending inputs even
    while scripts are paused (e.g. xbox_passthrough, capture keyboard mode).
    """
    _tls.bypass_gate = True


def _should_gate() -> bool:
    """Return True if the current thread should wait on the gate."""
    return not getattr(_tls, "bypass_gate", False)


def pause() -> None:
    """Pause all non-bypass threads at their next click/hold_for call."""
    switch_gate().pause()
    print(f"{Fore.YELLOW}[PAUSED]{Style.RESET_ALL}")


def resume() -> None:
    """Resume all waiting script threads."""
    switch_gate().run()
    print(f"{Fore.GREEN}[RESUMED]{Style.RESET_ALL}")


def toggle_pause() -> None:
    """Toggle between paused and running."""
    switch_gate().toggle_pause()
    state = "PAUSED" if not switch_gate().is_running else "RESUMED"
    print(
        f"{Fore.YELLOW if state == 'PAUSED' else Fore.GREEN}[{state}]{Style.RESET_ALL}"
    )


def is_paused() -> bool:
    """Return True if the gate is currently closed."""
    return not switch_gate().is_running


def connect(port=None, baudrate=None, timeout=0.1):
    """Open the serial connection. Call once at startup.

    dsrdtr=True / rtscts=False keeps DTR asserted so the Arduino does NOT
    reset when the port opens, avoiding the ~2 s bootloader delay.
    """
    global _arduino, _port, _baudrate, _timeout

    if _arduino != None and not typing.cast(serial.Serial, _arduino).closed:
        return

    _port = _port or port or settings.COM_PORT
    _baudrate = _baudrate or baudrate or settings.BAUDRATE
    _timeout = _timeout or timeout

    _arduino = serial.Serial(
        port=_port,
        baudrate=_baudrate,
        timeout=_timeout,
    )
    # threading.Thread(target=thread_read_serial, daemon=True).start()


def _write(data, first=True):

    if _arduino == None:
        connect()
    try:
        _arduino.write(data)  # type: ignore
    except Exception as e:
        connect()
        if first:
            _write(data, first=False)
        else:
            print(e)


def thread_read_serial():
    """Background thread to read and print serial input"""
    global _arduino
    print(f"{Fore.GREEN}Connected to Arduino on {settings.COM_PORT}{Style.RESET_ALL}")
    if _arduino is None:
        raise RuntimeError("Arduino not connected; call connect() first")
    _write("V".encode())
    try:
        while True:
            line = _arduino.readline().decode(errors="ignore").strip()
            if line:
                print(f"{Fore.CYAN}ARDUINO:{Style.RESET_ALL}", line)
            time.sleep(0.01)
    except:
        exit(-1)


def click(key, sleep=0.5, repeat=1):
    """Send a momentary button press (key down → 0.1 s → key up)."""
    if _should_gate():
        switch_gate().wait()
    if settings.PRINT_KEY:
        print(
            f"{Fore.YELLOW}KEY:{Style.RESET_ALL}",
            key + (" x" + str(repeat) if repeat > 1 else ""),
        )
    if _arduino is None:
        raise RuntimeError("Arduino not connected; call connect() first")
    for _ in range(repeat):
        hold(key)
        time.sleep(0.2)
        release(key)
        time.sleep(sleep)


def hold(key):
    """Send a key down signal without releasing it."""
    if settings.PRINT_KEY:
        print(f"{Fore.YELLOW}KEY DOWN:{Style.RESET_ALL}", key)
    if _arduino is None:
        raise RuntimeError("Arduino not connected; call connect() first")
    _write(key.encode())
    _write("1".encode())  # '1' means "key down" in the Arduino code


def release(key):
    """Send a key up signal."""
    if settings.PRINT_KEY:
        print(f"{Fore.YELLOW}KEY UP:{Style.RESET_ALL}", key)
    if _arduino is None:
        raise RuntimeError("Arduino not connected; call connect() first")
    _write(key.encode())
    _write("0".encode())  # '0' means "key up" in the Arduino code


def hold_for(key, duration, sleep=0.1):
    """Hold a key for `duration` seconds, then release."""
    if _should_gate():
        switch_gate().wait()
    if _arduino is None:
        raise RuntimeError("Arduino not connected; call connect() first")
    hold(key)
    time.sleep(duration)
    release(key)
    time.sleep(sleep)


def set_left_stick(x: float, y: float):
    """Set left stick to a normalised position (-1.0 to 1.0).
    Sends '<' + x_byte + y_byte (3 bytes). Call with (0.0, 0.0) to centre.
    No-ops if the change from the last sent value is below _STICK_THRESHOLD."""
    global _left_last_bx, _left_last_by
    if _arduino is None:
        raise RuntimeError("Arduino not connected; call connect() first")
    bx = max(0, min(255, round((x + 1.0) * 127.5)))
    by = max(0, min(255, round((y + 1.0) * 127.5)))
    if (
        abs(bx - _left_last_bx) < _STICK_THRESHOLD
        and abs(by - _left_last_by) < _STICK_THRESHOLD
    ) and not (bx == 127.5 or by == 127.5):
        return False
    _left_last_bx, _left_last_by = bx, by
    _write(bytes([ord("<"), bx, by]))
    _arduino.flush()


def set_right_stick(x: float, y: float):
    """Set right stick to a normalised position (-1.0 to 1.0).
    Sends '>' + x_byte + y_byte (3 bytes). Call with (0.0, 0.0) to centre.
    No-ops if the change from the last sent value is below _STICK_THRESHOLD."""
    global _right_last_bx, _right_last_by
    if _arduino is None:
        raise RuntimeError("Arduino not connected; call connect() first")
    bx = max(0, min(255, round((x + 1.0) * 127.5)))
    by = max(0, min(255, round((y + 1.0) * 127.5)))
    if (
        abs(bx - _right_last_bx) < _STICK_THRESHOLD
        and abs(by - _right_last_by) < _STICK_THRESHOLD
    ) or (bx == 127.5 and by == 127.5):
        return False
    _right_last_bx, _right_last_by = bx, by
    _write(bytes([ord(">"), bx, by]))
    _arduino.flush()
