"""
controller.py — Arduino serial pseudo-library.

Manages the serial connection to the Arduino and exposes press/hold helpers.

Usage
-----
    import controller
    controller.connect("COM6")   # or pass port=None to use the default
    controller.press("A", sleep=0.5, repeat=3)
    controller.hold("w", duration=1.2)
"""

import time

import serial
import settings
from colorama import Fore, Style

_arduino = None


def connect(port=None, baudrate=None, timeout=0.1):
    """Open the serial connection. Call once at startup.

    dsrdtr=True / rtscts=False keeps DTR asserted so the Arduino does NOT
    reset when the port opens, avoiding the ~2 s bootloader delay.
    """
    global _arduino
    _arduino = serial.Serial(
        port=port or settings.COM_PORT,
        baudrate=baudrate or settings.BAUDRATE,
        timeout=timeout,
        dsrdtr=True, rtscts=False,
    )


def press(key, sleep=0.5, repeat=1):
    """Send a momentary button press (key down → 0.1 s → key up)."""
    if settings.PRINT_KEY:
        print(
            f"{Fore.YELLOW}KEY:{Style.RESET_ALL}",
            key + (" x" + str(repeat) if repeat > 1 else ""),
        )
    if _arduino is None:
        raise RuntimeError("Arduino not connected; call connect() first")
    for _ in range(repeat):
        _arduino.write(key.encode())
        time.sleep(0.1)
        _arduino.write(b"0")
        time.sleep(sleep)


def hold(key, duration, sleep=0.1):
    """Hold a key for `duration` seconds, then release."""
    if _arduino is None:
        raise RuntimeError("Arduino not connected; call connect() first")
    _arduino.write(key.encode())
    time.sleep(duration)
    _arduino.write(b"0")
    time.sleep(sleep)
