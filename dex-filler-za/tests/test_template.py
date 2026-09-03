"""
Checks on the badge template itself.

The template must stay a **crop of a real screenshot** with everything outside
the badge's white outline made transparent -- not a mask drawn from thresholds,
not an average of instances. These tests fail if it drifts back to either.
"""

from __future__ import annotations

import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import dex_za_vision as v  # noqa: E402

_FRAMES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reference_frames")

# The slot the template was cut from: last column, penultimate row.  Its Golbat
# has nothing white against the badge, so the outline extracts unambiguously.
SOURCE_FRAME = "box_full_badges"
SOURCE_SLOT = (4, 6)


def frame(name: str):
    img = cv2.imread(os.path.join(_FRAMES, f"{name}.png"))
    assert img is not None, name
    return img


def stored():
    bgra = cv2.imread(v.TEMPLATE_PATH, cv2.IMREAD_UNCHANGED)
    assert bgra is not None, v.TEMPLATE_PATH
    return bgra


def test_template_carries_an_alpha_channel():
    assert stored().shape[2] == 4


def test_template_is_cut_from_the_reference_screenshot():
    """Re-cutting from the source frame must reproduce the stored file exactly.

    This is what pins the template to real screenshot pixels: if someone
    replaces it with a hand-built or averaged image, this fails.
    """
    win = v.capture.get_frame_region(
        *v.badge_roi(*SOURCE_SLOT), frame=frame(SOURCE_FRAME)
    )
    shape = v.badge_shape(win)
    ys, xs = np.nonzero(shape)
    box = (slice(ys.min(), ys.max() + 1), slice(xs.min(), xs.max() + 1))
    expected = np.dstack([win[box], shape[box]])
    assert np.array_equal(stored(), expected)


def test_everything_outside_the_outline_is_transparent():
    """The opaque region is the glyph: lime fill, white outline, and the pixels
    enclosed by it -- and nothing beyond."""
    bgra = stored()
    opaque = bgra[:, :, 3] > 0
    assert opaque.any() and not opaque.all(), "template is all or nothing"

    # The glyph touches every edge of its own bounding box.
    assert opaque[0].any() and opaque[-1].any()
    assert opaque[:, 0].any() and opaque[:, -1].any()

    # Its border pixels are the white outline, not stray background.
    border = v.white_mask(bgra[:, :, :3]) > 0
    edge = np.zeros_like(opaque)
    edge[0], edge[-1], edge[:, 0], edge[:, -1] = True, True, True, True
    on_edge = opaque & edge
    assert (border & on_edge).sum() / on_edge.sum() > 0.5


def test_the_glyph_is_solid_with_no_speckle_holes():
    """Filling the outline is what stops the mask coming out as scattered
    colour-band fragments, which is not the shape drawn on screen."""
    opaque = (stored()[:, :, 3] > 0).astype(np.uint8)
    filled = np.zeros_like(opaque)
    contours, _ = cv2.findContours(opaque, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(filled, contours, -1, 1, thickness=cv2.FILLED)
    assert np.array_equal(opaque, filled), "the mask has holes inside its outline"


def test_the_correlated_shape_is_the_lime_fill():
    """What gets matched is the lime fill keyed out of the template's own real
    pixels, so the frame and the template are keyed the same way."""
    tpl = v.load_template()
    bgra = stored()
    assert tpl.shape == bgra.shape[:2]
    assert 0 < np.count_nonzero(tpl) < np.count_nonzero(bgra[:, :, 3])


def test_badge_shape_ignores_white_that_does_not_touch_the_badge():
    """Growing outward from the lime fill is deliberate.  Taking every white
    pixel in the window would swallow a pale sprite sharing the corner."""
    win = v.capture.get_frame_region(
        *v.badge_roi(*SOURCE_SLOT), frame=frame(SOURCE_FRAME)
    )
    detached = win.copy()
    detached[0:4, 0:4] = (255, 255, 255)   # a white blob in the far corner
    assert not v.badge_shape(detached)[0:4, 0:4].any()
