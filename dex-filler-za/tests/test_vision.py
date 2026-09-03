"""
Offline checks for the Z-A link-trade screen reading.

Every fixture is a real 1920x1080 capture-card frame taken off the Switch, so
these run with no console, Arduino or capture card attached.

Ground truth for the badge sets was read off the frames by eye and cross-checked
against a template-independent colour-fill measure; the empty/full classifications
come from the box sweep, where boxes 1-12 were full, box 13 held nine Pokemon and
boxes 14-32 were bare.
"""

from __future__ import annotations

import os
import sys

import cv2
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import dex_za_vision as v  # noqa: E402

_FRAMES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reference_frames")


def frame(name: str):
    path = os.path.join(_FRAMES, f"{name}.png")
    img = cv2.imread(path)
    assert img is not None, f"fixture missing: {path}"
    return img


# ---------------------------------------------------------------------------
# Badges
# ---------------------------------------------------------------------------
def test_badges_on_a_full_box():
    assert v.scan_page(frame("box_full_badges")) == {(2, 6), (4, 3), (4, 4), (4, 5), (4, 6)}


def test_badges_survive_the_cursor_sitting_on_the_party():
    """Parking the cursor on the party must not disturb the badge read.

    This is why the loop parks there: the name tooltip is drawn above the
    selected tile, and from a box row it would cover the badges of the row
    above.  Above the party it lands clear of the grid entirely.  Both fixtures
    show the same box, so the badge sets must agree.
    """
    assert v.scan_page(frame("cursor_party_c6")) == v.scan_page(frame("box_full_badges"))


def test_green_pokemon_are_not_mistaken_for_badges():
    """The box whose lime-green sprites came closest to passing.

    Across all 32 boxes the near-misses scored at most 0.396 on shape against a
    worst real badge of 0.726, and at most 0.089 on the white outline against a
    worst real 0.330.
    """
    assert v.scan_page(frame("box_green_mons")) == {(1, 2), (1, 3), (1, 5), (1, 6), (2, 4)}


def test_no_badges_on_an_empty_box():
    assert v.scan_page(frame("box_empty")) == set()


@pytest.mark.parametrize("cell", [(2, 6), (4, 3), (4, 4), (4, 5), (4, 6)])
def test_real_badges_clear_both_thresholds_with_margin(cell):
    present, score, outline = v.badge_at(frame("box_full_badges"), *cell)
    assert present
    assert score >= 0.70, f"shape score {score:.3f} too close to the 0.55 threshold"
    assert outline >= 0.30, f"outline ratio {outline:.3f} too close to the 0.25 threshold"


# ---------------------------------------------------------------------------
# Cursor
# ---------------------------------------------------------------------------
def test_cursor_on_the_party_row():
    assert v.find_cursor(frame("cursor_party_c6")) == (0, 6)


def test_cursor_on_a_box_row():
    assert v.find_cursor(frame("box_full_badges")) == (1, 1)


def test_no_cursor_during_the_trade_animation():
    assert v.find_cursor(frame("trade_flash")) is None


# ---------------------------------------------------------------------------
# Empty box -- the stop condition
# ---------------------------------------------------------------------------
def test_empty_box_is_recognised():
    assert v.page_is_empty(frame("box_empty"))


def test_partly_filled_box_is_not_empty():
    """Box 13 holds nine Pokemon.  It must not read as the stop condition."""
    assert not v.page_is_empty(frame("box_partial"))


def test_full_box_is_not_empty():
    assert not v.page_is_empty(frame("box_full_badges"))


def test_a_box_with_no_badges_is_not_the_stop_condition():
    """The filler stops on an empty box, never on a box that merely has no
    badges left -- a complete box legitimately shows none."""
    f = frame("box_partial")
    assert not v.page_is_empty(f)


# ---------------------------------------------------------------------------
# Screen state
# ---------------------------------------------------------------------------
def test_box_screen_detected_on_a_full_box():
    assert v.on_box_screen(frame("box_full_badges"))


def test_box_screen_detected_on_an_empty_box():
    """The signal must not depend on the box holding anything: the cursor is
    invisible on an empty box, and that is the page the loop has to recognise."""
    assert v.on_box_screen(frame("box_empty"))


def test_box_screen_not_detected_during_the_trade():
    assert not v.on_box_screen(frame("trade_flash"))


def test_trade_flash_detected():
    assert v.is_trade_flash(frame("trade_flash"))


def test_trade_flash_not_detected_on_the_box_screen():
    for name in ("box_full_badges", "box_partial", "box_empty", "cursor_party_c6"):
        assert not v.is_trade_flash(frame(name)), name


# ---------------------------------------------------------------------------
# The full capture set, when it is around
# ---------------------------------------------------------------------------
_SWEEP = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..",
    "tera-raid-farmer", "calib_frames",
)


def _exact_badge_rect(row: int, col: int):
    x1, y1, _, _ = v.tile_box(row, col)
    return (
        int(x1 + v.BADGE_X * v.CELL), int(y1 + v.BADGE_Y * v.CELL),
        int(x1 + (v.BADGE_X + v.BADGE_W) * v.CELL),
        int(y1 + (v.BADGE_Y + v.BADGE_H) * v.CELL),
    )


def test_badge_set_is_exact_on_every_captured_box():
    """Runs over the whole box sweep if the scratch captures are still present.

    Ground truth here is independent of the template: a slot counts as badged
    when its exact badge rectangle is at least 40% badge-coloured, which
    separates cleanly (real badges fill 0.534 of it, nothing else exceeds
    0.164).  Skipped when the captures have been cleaned up -- they are
    gitignored, so this cannot run on a fresh clone.
    """
    import glob

    import numpy as np

    frames = sorted(glob.glob(os.path.join(_SWEEP, "shot_box*.png")))
    if not frames:
        pytest.skip("box sweep captures not present")

    wrong = []
    for path in frames:
        img = cv2.imread(path)
        want = set()
        for cell in v.all_cells():
            crop = v.capture.get_frame_region(*_exact_badge_rect(*cell), frame=img)
            fill = np.count_nonzero(v.badge_mask(crop)) / crop[:, :, 0].size
            if fill >= 0.40:
                want.add(cell)
        got = v.scan_page(img)
        if got != want:
            wrong.append((os.path.basename(path), sorted(got), sorted(want)))

    assert not wrong, f"{len(wrong)} of {len(frames)} frames misread: {wrong[:3]}"
