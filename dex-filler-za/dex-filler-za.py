"""
dex-filler-za.py — Pokemon Legends Z-A link-trade dex filler.

Trades away the Pokemon the other side still needs, box by box, driving the
Switch over the Arduino and reading the screen off the capture card.

How it decides what to trade
----------------------------
On the LINK TRADE box screen the game marks, with a green thumbs-up badge, the
slots holding a Pokemon that is missing from the *partner's* dex.  That marking
is a snapshot: it does not refresh after a trade.  Measured directly -- box 1
showed badges on (2,6), (4,3), (4,4), (4,5), (4,6); after trading (2,6) the very
next frame showed (4,3), (4,4), (4,5), (4,6) *and a new one on (5,1)*.  So the
set does not merely lose the slot you traded, it changes.

The loop therefore commits to one read per page:

    park the cursor on the party
    read the badges once
    trade exactly those slots, in reading order, never re-reading, never going back
    park again, press R, repeat

and it stops on the first box holding **no Pokemon at all** -- not the first box
without badges, because a complete box legitimately shows none.

The cursor is parked on the party for the read because the game draws the
selected Pokemon's name in a tooltip *above* its tile.  From a box row that
tooltip covers the bottom of the row above, which is exactly where the badges
sit.  Above the party it clears the grid entirely.

Timing
------
Waits are on events, not clocks.  What was measured on one real trade: the
animation's opening flash came 13.6 s after the first A press (a window only
0.5 s wide, hence the dense polling), and the box screen was back some time
between 25 s and 139 s later -- the capture did not cover that gap, so the
figure is a bound, not a measurement, and the code waits for the screen instead
of assuming a duration.

Keys, in the capture window
---------------------------
    d   dump what the detector currently sees (badges, cursor, empty, screen)
    t   re-cut the badge template from the live frame at the cursor
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from switch import Fore, Style, capture, click, launch, notifier  # noqa: E402

import dex_za_nav as nav  # noqa: E402
import dex_za_vision as v  # noqa: E402

_DIR = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
_DEFAULTS = {
    "com_port": None,
    "start_box": 13,
    "dpad_gap": nav.DPAD_GAP,
    "a_gap": 0.25,
    "flash_timeout": 90.0,
    "return_timeout": 240.0,
    "poll_interval": 0.05,
    "settle": 0.35,
    "max_trades": 0,
}


def _load_config() -> dict:
    cfg = dict(_DEFAULTS)
    try:
        with open(os.path.join(_DIR, "config.json"), encoding="utf-8") as f:
            cfg.update(json.load(f))
    except FileNotFoundError:
        pass
    return cfg


CFG = _load_config()


# ---------------------------------------------------------------------------
# Debug overlay: every badge window, plus the regions the screen tests use
# ---------------------------------------------------------------------------
def _debug_regions() -> list:
    regions = []
    for row, col in v.all_cells():
        x1, y1, x2, y2 = v.badge_roi(row, col)
        regions.append((f"{row}{col}", x1, y1, x2, y2, (0, 200, 255)))
    for col in range(1, v.COLS + 1):
        x1, y1, x2, y2 = v.tile_box(0, col)
        regions.append((f"p{col}", x1, y1, x2, y2, (255, 120, 0)))
    regions.append(("hint", *v._HINT_BAR, (255, 0, 255)))
    return regions


_DEBUG_REGIONS = _debug_regions()


# ---------------------------------------------------------------------------
# Screen access
# ---------------------------------------------------------------------------
def _frame():
    frame = capture.get_latest_frame()
    if frame is None:
        raise RuntimeError("no frame from the capture card yet")
    return frame


def _read_cursor():
    return v.find_cursor(_frame())


def _press(key: str) -> None:
    click(key, sleep=CFG["dpad_gap"])


def _park():
    """Put the cursor on party slot 1, where the tooltip clears the grid."""
    return nav.goto(nav.PARTY_SLOT_1, _read_cursor, _press)


def _wait_for_box_screen(timeout: float, consecutive: int = 3):
    """Block until the box screen has been up for `consecutive` reads.

    More than one read is required because a single mid-trade frame did briefly
    show the box screen; latching onto the first hit would resume too early.
    """
    deadline = time.time() + timeout
    hits = 0
    while time.time() < deadline:
        if v.on_box_screen(_frame()):
            hits += 1
            if hits >= consecutive:
                time.sleep(CFG["settle"])
                return _frame()
        else:
            hits = 0
        time.sleep(CFG["poll_interval"])
    raise RuntimeError(f"box screen did not come back within {timeout:.0f}s")


# ---------------------------------------------------------------------------
# Trading
# ---------------------------------------------------------------------------
def _spam_a_until_flash(timeout: float) -> None:
    """Press A until the trade animation starts, then stop.

    Pressing A before the commit is harmless -- the other side is a turbo
    controller doing the confirming -- but pressing it afterwards is not: once
    the box screen is back, a stray A would select a Pokemon and start another
    trade.  So the flash is polled densely between presses rather than checked
    once per press: its window measured only half a second.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        click("A", sleep=0)
        # Check once per press before waiting, never only inside the gap loop:
        # with a short a_gap that loop can be skipped entirely, which would
        # leave A hammering away through the whole animation.
        if v.is_trade_flash(_frame()):
            return
        settle = time.time() + CFG["a_gap"]
        while time.time() < settle:
            if v.is_trade_flash(_frame()):
                return
            time.sleep(CFG["poll_interval"])
    raise RuntimeError(f"no trade flash within {timeout:.0f}s -- is the other side confirming?")


def _trade_current_slot() -> None:
    _spam_a_until_flash(CFG["flash_timeout"])
    _wait_for_box_screen(CFG["return_timeout"])


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main() -> None:
    box = CFG["start_box"]
    traded = 0

    notifier.send_message(f"Z-A dex filler a arrancar na caixa {box}")
    _wait_for_box_screen(timeout=30.0)

    while True:
        _park()
        frame = _frame()

        if v.page_is_empty(frame):
            print(f"{Fore.GREEN}Caixa {box} vazia — fim.{Style.RESET_ALL}")
            notifier.send_message(
                f"Terminado: caixa {box} sem Pokemon. {traded} trocas feitas."
            )
            return

        snapshot = sorted(v.scan_page(frame))
        print(f"{Fore.CYAN}Caixa {box}:{Style.RESET_ALL} {len(snapshot)} para trocar {snapshot}")

        for cell in snapshot:
            if CFG["max_trades"] and traded >= CFG["max_trades"]:
                notifier.send_message(f"Limite de {CFG['max_trades']} trocas atingido.")
                return
            nav.goto(cell, _read_cursor, _press)
            print(f"  trocar {cell}")
            _trade_current_slot()
            traded += 1

        notifier.send_message(
            f"Caixa {box} feita: {len(snapshot)} trocas. Total {traded}."
        )
        _park()
        click("R", sleep=0.6)
        box += 1


# ---------------------------------------------------------------------------
# Calibration keys
# ---------------------------------------------------------------------------
def _key_handler(key, frame):
    if key == ord("d"):
        cursor = v.find_cursor(frame)
        print(f"{Fore.CYAN}cursor{Style.RESET_ALL} {cursor}")
        print(f"{Fore.CYAN}box screen{Style.RESET_ALL} {v.on_box_screen(frame)}"
              f"   {Fore.CYAN}flash{Style.RESET_ALL} {v.is_trade_flash(frame)}"
              f"   {Fore.CYAN}empty{Style.RESET_ALL} {v.page_is_empty(frame)}")
        for row in range(1, v.ROWS + 1):
            cells = []
            for col in range(1, v.COLS + 1):
                present, score, outline = v.badge_at(frame, row, col)
                mark = "#" if present else "."
                cells.append(f"{mark}{score:5.2f}/{outline:4.2f}")
            print(f"  r{row} " + "  ".join(cells))
    elif key == ord("t"):
        cursor = v.find_cursor(frame)
        if cursor is None or cursor[0] == 0:
            print("point the cursor at a box slot that shows a badge first")
            return
        mask = v.make_template(frame, *cursor)
        print(f"badge template re-cut from {cursor}: {mask.shape}, saved to {v.TEMPLATE_PATH}")


if __name__ == "__main__":
    launch(main, debug_regions=_DEBUG_REGIONS, key_callback=_key_handler, port=CFG["com_port"])
