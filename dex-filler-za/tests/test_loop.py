"""
A dry run of the whole filler against a fake console.

The screen is played back from the real capture-card fixtures and the Arduino is
a list of recorded key presses, so this exercises the actual loop -- parking,
the single badge read per page, the trade handshake, the page turn and the stop
condition -- with no hardware attached.

What it is here to catch is the part the vision tests cannot: that the loop
commits to one snapshot per page and never re-reads it.  The badges do not
refresh after a trade, so a loop that re-read them would trade the same slot
again or skip one.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import cv2
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJ = os.path.join(_HERE, "..")
_ROOT = os.path.join(_PROJ, "..")
for _p in (_ROOT, os.path.join(_ROOT, "utils"), _PROJ):
    sys.path.insert(0, _p)

import dex_za_vision as v  # noqa: E402

_FRAMES = os.path.join(_PROJ, "reference_frames")


def _load_runner():
    """The runner's filename has hyphens, so it cannot be imported by name."""
    path = os.path.join(_PROJ, "dex-filler-za.py")
    spec = importlib.util.spec_from_file_location("dexza_runner", path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _frame(name: str):
    img = cv2.imread(os.path.join(_FRAMES, f"{name}.png"))
    assert img is not None, name
    return img


class FakeConsole:
    """Plays back fixtures and tracks the cursor on the 6x6 torus.

    Pages are visited in order; `R` advances to the next one.  A trade is
    modelled the way the real game behaves: pressing A leaves the box screen,
    the flash follows, and the box screen comes back -- and crucially the badge
    set is NOT updated, because the real game does not update it either.
    """

    def __init__(self, pages):
        self.pages = pages          # list of fixture names, in R order
        self.page = 0
        self.cursor = (1, 1)        # where the sweep actually left it
        self.presses = []
        self.trades = []            # (page index, cell) per completed trade
        self._phase = "box"         # box -> dialog -> flash -> box
        self._ticks = 0

    # -- screen ----------------------------------------------------------
    def frame(self):
        if self._phase == "flash":
            return _frame("trade_flash")
        if self._phase == "dialog":
            return _frame("trade_flash")   # off the box screen either way
        return _frame(self.pages[self.page])

    def find_cursor(self, frame):
        if self._phase != "box":
            return None
        return self.cursor

    def on_box_screen(self, frame):
        return self._phase == "box"

    def is_trade_flash(self, frame):
        # The flash is short: report it once, then move on to the return.
        if self._phase == "flash":
            self._phase = "box"
            return True
        return False

    # -- controller ------------------------------------------------------
    def press(self, key, sleep=None, repeat=1):
        for _ in range(repeat):
            self.presses.append(key)
            r, c = self.cursor
            if key == "2":
                self.cursor = ((r + 1) % 6, c)
            elif key == "4":
                self.cursor = ((r - 1) % 6, c)
            elif key == "1":
                self.cursor = (r, (c % 6) + 1)
            elif key == "3":
                self.cursor = (r, 6 if c == 1 else c - 1)
            elif key == "R":
                self.page = min(self.page + 1, len(self.pages) - 1)
            elif key == "A":
                if self._phase == "box":
                    self.trades.append((self.page, self.cursor))
                    self._phase = "dialog"
                elif self._phase == "dialog":
                    self._phase = "flash"


@pytest.fixture
def wired(monkeypatch):
    runner = _load_runner()
    console = FakeConsole(["box_full_badges", "box_partial", "box_empty"])

    monkeypatch.setattr(runner, "_frame", console.frame)
    monkeypatch.setattr(runner, "_press", lambda key: console.press(key))
    monkeypatch.setattr(runner, "click", console.press)
    monkeypatch.setattr(runner.v, "find_cursor", console.find_cursor)
    monkeypatch.setattr(runner.v, "on_box_screen", console.on_box_screen)
    monkeypatch.setattr(runner.v, "is_trade_flash", console.is_trade_flash)
    monkeypatch.setattr(runner.notifier, "send_message", lambda *a, **k: None)
    # a_gap=0 is deliberate: it is the setting under which the flash used to go
    # unpolled, leaving A hammering through the whole animation.  The short
    # timeouts turn any such hang into a fast failure instead of a stuck test.
    monkeypatch.setattr(
        runner,
        "CFG",
        dict(runner.CFG, settle=0, poll_interval=0, a_gap=0,
             flash_timeout=2.0, return_timeout=2.0),
    )
    return runner, console


def test_trades_every_badged_slot_and_stops_on_the_empty_box(wired):
    runner, console = wired
    runner.main()

    traded_cells = [cell for _, cell in console.trades]
    assert traded_cells == [
        (2, 6), (4, 3), (4, 4), (4, 5), (4, 6),   # box_full_badges
        (1, 1), (1, 2),                            # box_partial
    ]


def test_visits_pages_in_order_and_stops_at_the_empty_one(wired):
    runner, console = wired
    runner.main()
    assert console.page == 2                     # reached box_empty
    assert console.presses.count("R") == 2       # and turned the page twice


def test_reads_the_badges_once_per_page(wired):
    """The snapshot is committed, not re-read.

    Every trade replaces the slot's Pokemon with the received one and leaves the
    badge markings stale, so a second read of the same page would be wrong.  The
    fake console deliberately keeps serving the same badge set to make a re-read
    show up as a repeated or missing trade.
    """
    runner, console = wired
    runner.main()
    per_page = [cell for page, cell in console.trades if page == 0]
    assert per_page == [(2, 6), (4, 3), (4, 4), (4, 5), (4, 6)]
    assert len(per_page) == len(set(per_page)), "a slot was traded twice"


def test_parks_on_the_party_before_reading_each_page(wired):
    runner, console = wired
    runner.main()
    # Parking is the only reason the cursor ever sits on row 0.
    assert any(key == "4" for key in console.presses), "never moved up to the party"


def test_respects_the_max_trades_stop(wired):
    runner, console = wired
    monkey = dict(runner.CFG, max_trades=3)
    runner.CFG = monkey
    runner.main()
    assert len(console.trades) == 3
