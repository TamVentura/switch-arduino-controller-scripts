"""
dex_za_nav.py — moving the box cursor around the Z-A link-trade screen.

The screen is a 6x6 torus: row 0 is the party, rows 1..5 are the box grid, and
both axes wrap independently.  This was checked one press at a time on all six
rows: RIGHT on column 6 always lands on column 1 of the *same* row and never
carries into the next one, LEFT on column 1 goes to column 6, and UP on row 0
goes to row 5.

Because the axes wrap, the shortest route between any two slots is at most three
presses per axis -- six in total, against up to ten for naive stepping.

Nothing here talks to the Arduino or the capture card directly: the driving
functions take a "read the cursor" and a "press a key" callable, which keeps the
whole module testable offline and lets the caller decide about timing.
"""

from __future__ import annotations

from typing import Callable, Iterable

Cell = tuple[int, int]

# D-pad tokens understood by controller.click (see controls.txt).
RIGHT, DOWN, LEFT, UP = "1", "2", "3", "4"

ROWS = 6   # party + 5 box rows
COLS = 6

PARTY_SLOT_1: Cell = (0, 1)

# Minimum gap between two presses of the SAME direction, in seconds.
#
# Repeating one direction is the fragile case: 24 identical RIGHT presses came
# back clean at gaps of 0.06 (three runs), 0.05 and 0.04, but dropped presses at
# 0.03 and below.  Alternating directions survived 0.02, so the limit is the
# game coalescing fast repeats rather than raw throughput.  0.06 keeps a margin
# over the 0.04/0.03 boundary, and the closed loop below recovers anyway.
DPAD_GAP = 0.06


def shortest_path(src: Cell, dst: Cell) -> list[str]:
    """The fewest d-pad presses that move the cursor from ``src`` to ``dst``.

    Each axis is walked whichever way round is shorter, which is what the wrap
    buys us.  Vertical first, so a caller watching the screen sees the row
    settle before the column.
    """
    keys: list[str] = []

    down = (dst[0] - src[0]) % ROWS
    if down:
        keys += [DOWN] * down if down <= ROWS // 2 else [UP] * (ROWS - down)

    right = (dst[1] - src[1]) % COLS
    if right:
        keys += [RIGHT] * right if right <= COLS // 2 else [LEFT] * (COLS - right)

    return keys


def goto(
    target: Cell,
    read_cursor: Callable[[], Cell | None],
    press: Callable[[str], None],
    *,
    max_rounds: int = 4,
) -> Cell:
    """Move the cursor to ``target``, checking the screen rather than trusting it.

    Presses the whole route, then re-reads where the cursor actually landed and
    tries again if it is not there yet.  Counting presses open-loop is not good
    enough: a press does occasionally get eaten, and a cursor one slot off the
    intended one means offering the wrong Pokemon.  Moving the cursor is
    harmless, so retrying costs nothing but time.

    Raises RuntimeError if the cursor cannot be read at all, or if the target is
    still not reached after ``max_rounds`` attempts.
    """
    for attempt in range(max_rounds):
        current = read_cursor()
        if current is None:
            raise RuntimeError(
                f"cannot see the cursor while heading for {target} "
                f"(attempt {attempt + 1}/{max_rounds}) -- is the box screen up?"
            )
        if current == target:
            return current
        for key in shortest_path(current, target):
            press(key)

    final = read_cursor()
    raise RuntimeError(
        f"cursor stuck at {final}, wanted {target} after {max_rounds} rounds"
    )


def press_all(keys: Iterable[str], press: Callable[[str], None]) -> None:
    """Send a route produced by ``shortest_path``."""
    for key in keys:
        press(key)
