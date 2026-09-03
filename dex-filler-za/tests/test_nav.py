"""
Checks for cursor routing on the 6x6 link-trade torus.

The wrap behaviour asserted here was measured on the console one press at a
time, across all six rows: horizontal wrap stays in its row, vertical wrap
carries the party as row 0.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import dex_za_nav as nav  # noqa: E402


# ---------------------------------------------------------------------------
# shortest_path
# ---------------------------------------------------------------------------
def test_no_presses_when_already_there():
    assert nav.shortest_path((3, 4), (3, 4)) == []


def test_straight_down():
    assert nav.shortest_path((1, 2), (3, 2)) == [nav.DOWN, nav.DOWN]


def test_straight_right():
    assert nav.shortest_path((2, 1), (2, 3)) == [nav.RIGHT, nav.RIGHT]


def test_wraps_upward_when_that_is_shorter():
    """Row 1 to row 5 is four steps down but two steps up, via the party."""
    assert nav.shortest_path((1, 1), (5, 1)) == [nav.UP, nav.UP]


def test_wrapping_forward_off_the_last_column():
    """Column 6 to column 1 is a single RIGHT, because RIGHT wraps within the
    row -- measured on all six rows, and it never carries into the next one."""
    assert nav.shortest_path((2, 6), (2, 1)) == [nav.RIGHT]


def test_wraps_leftward_when_that_is_shorter():
    """Column 1 to column 6 is one press left, not five right."""
    assert nav.shortest_path((2, 1), (2, 6)) == [nav.LEFT]


def test_both_axes_at_once():
    assert nav.shortest_path((0, 1), (1, 2)) == [nav.DOWN, nav.RIGHT]


def test_never_more_than_three_presses_per_axis():
    """The whole point of exploiting the wrap: six presses covers any move."""
    cells = [(r, c) for r in range(6) for c in range(1, 7)]
    worst = max(len(nav.shortest_path(a, b)) for a in cells for b in cells)
    assert worst == 6


def test_row_zero_is_reachable_from_every_row():
    """Parking on the party is what the filler does between pages."""
    for row in range(6):
        route = nav.shortest_path((row, 3), nav.PARTY_SLOT_1)
        assert len(route) <= 6


# ---------------------------------------------------------------------------
# goto -- the closed loop
# ---------------------------------------------------------------------------
class FakeScreen:
    """A cursor on the same torus, which may swallow presses.

    ``eat`` is a list of press indices to ignore, which is how a dropped d-pad
    input is reproduced.
    """

    def __init__(self, start, eat=()):
        self.cell = start
        self.eat = set(eat)
        self.presses = 0

    def read(self):
        return self.cell

    def press(self, key):
        index = self.presses
        self.presses += 1
        if index in self.eat:
            return
        r, c = self.cell
        if key == nav.DOWN:
            r = (r + 1) % 6
        elif key == nav.UP:
            r = (r - 1) % 6
        elif key == nav.RIGHT:
            c = (c % 6) + 1
        elif key == nav.LEFT:
            c = 6 if c == 1 else c - 1
        self.cell = (r, c)


def test_goto_arrives_and_stops_pressing():
    screen = FakeScreen((0, 1))
    assert nav.goto((2, 3), screen.read, screen.press) == (2, 3)
    assert screen.presses == 4  # two down, two right


def test_goto_recovers_from_a_dropped_press():
    """The reason goto re-reads instead of counting presses."""
    screen = FakeScreen((1, 1), eat=[0])
    assert nav.goto((1, 4), screen.read, screen.press) == (1, 4)
    assert screen.presses > 3  # it had to go round again


def test_goto_recovers_from_several_dropped_presses():
    screen = FakeScreen((5, 6), eat=[0, 2, 4])
    assert nav.goto((0, 1), screen.read, screen.press) == (0, 1)


def test_goto_gives_up_rather_than_pressing_forever():
    class Stuck(FakeScreen):
        def press(self, key):
            self.presses += 1  # every press ignored

    screen = Stuck((3, 3))
    with pytest.raises(RuntimeError, match="stuck"):
        nav.goto((1, 1), screen.read, screen.press)


def test_goto_refuses_to_move_blind():
    """No cursor means we are not on the box screen; guessing would offer the
    wrong Pokemon."""
    with pytest.raises(RuntimeError, match="cannot see the cursor"):
        nav.goto((1, 1), lambda: None, lambda key: None)
