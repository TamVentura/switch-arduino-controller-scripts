"""
dex_za_vision.py — screen reading for the Legends Z-A link-trade dex filler.

Everything here is a pure function of a captured frame, so it can be exercised
offline against saved frames (see ``tests/``) without a Switch, an Arduino or a
capture card attached.

Screen model
------------
The LINK TRADE box screen shows a 6-wide party row on top and a 6x5 box grid
below it.  Navigation wraps on both axes and the party counts as row 0, so the
whole thing is a 6x6 torus:

    row 0        party
    rows 1..5    box grid
    cols 1..6

All coordinates below are expressed in 4K (3840x2160) like the rest of the
project; ``capture.get_frame_region`` scales them to the live frame size.

What we read
------------
``scan_page``      which box slots carry the green "missing from the partner's
                   dex" thumbs-up badge
``find_cursor``    which slot is currently selected
``page_is_empty``  whether the visible box holds no Pokemon at all

The thumbs-up badge
-------------------
The badge is a flat lime-green thumbs-up with a white outline, drawn in the
bottom-right corner of the slot.  It breathes: a ~1.3 s animation that changes
its area by about 6% and never hides it.  A single frame is therefore enough to
see it, and the scale sweep below absorbs the breathing.

It is found by correlating the badge *shape* against a colour-keyed frame rather
than by matching raw pixels: the tile background runs through a strong gradient
down the grid, which wrecks plain BGR correlation, while the badge's own colour
is dead flat (hue 39 at the 50th, 95th and 99th percentile).  The white outline
is then verified separately -- see ``_outline_ratio``.

Over 64 captured frames covering all 32 boxes, this identifies the badge set
exactly on every one: real badges score >= 0.726 and nothing else exceeds 0.397,
with outline ratios of >= 0.330 against <= 0.089.  Matching the template's raw
pixels with ``template_matcher.find_all`` was measured against it and lost --
59/64 frames per cell, 53/64 across the panel, at any threshold or scale sweep.
"""

from __future__ import annotations

import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "utils"))

import capture  # noqa: E402

_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_PATH = os.path.join(_DIR, "templates", "thumbs_up.png")

# ---------------------------------------------------------------------------
# Grid geometry, in 4K coordinates
#
# Measured off the live capture: tile pitch 252, tile 228, so a 24 px gutter.
# The party tile is taller than a box tile because it also shows the level.
# ---------------------------------------------------------------------------
COL0 = 144          # left edge of column 1
PITCH = 252         # centre-to-centre distance between tiles, both axes
CELL = 228          # tile side
BOX_ROW0 = 756      # top edge of box row 1
PARTY_TOP = 300     # top edge of the party tiles
PARTY_HEIGHT = 316  # party tiles are taller (they carry the "Lv." label)

ROWS = 5            # box rows
COLS = 6

# Badge position inside a tile, as a fraction of the tile side.
BADGE_X = 0.614
BADGE_Y = 0.667
BADGE_W = 0.316
BADGE_H = 0.263

# Search window for the badge: the bottom-right corner of the tile, with slack
# on every side so a slightly mis-calibrated grid still contains the badge.
_ROI = (0.50, 1.04, 0.52, 1.04)

# ---------------------------------------------------------------------------
# Colour keys (HSV, OpenCV ranges: H 0-179, S 0-255, V 0-255)
#
# The badge fill is flat: hue 39, saturation 163, value 225 across every frame
# measured.  Keeping the hue band tight is what separates the badge from the
# many yellow-green sprites (Pikachu, Abra, Meowth): those sit around hue 28.
# ---------------------------------------------------------------------------
BADGE_HSV_LO = np.array([36, 120, 180])
BADGE_HSV_HI = np.array([42, 200, 255])

# The badge outline, and the selected tile, are both near-white.
WHITE_HSV_LO = np.array([0, 0, 190])
WHITE_HSV_HI = np.array([180, 60, 255])

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
BADGE_SCORE_MIN = 0.55      # shape correlation: real badges >= 0.726, nothing else > 0.397
BADGE_SCALES = (0.94, 1.0, 1.06)
OUTLINE_RATIO_MIN = 0.25    # fraction of the ring around the badge that is white
CURSOR_MARGIN_MIN = 40.0    # how far the selected tile must outshine the rest


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------
def tile_box(row: int, col: int) -> tuple[int, int, int, int]:
    """4K (x1, y1, x2, y2) of one tile.  Row 0 is the party row."""
    x1 = COL0 + (col - 1) * PITCH
    if row == 0:
        return x1, PARTY_TOP, x1 + CELL, PARTY_TOP + PARTY_HEIGHT
    y1 = BOX_ROW0 + (row - 1) * PITCH
    return x1, y1, x1 + CELL, y1 + CELL


def badge_roi(row: int, col: int) -> tuple[int, int, int, int]:
    """4K (x1, y1, x2, y2) of the corner window the badge lives in."""
    x1, y1, _, _ = tile_box(row, col)
    return (
        int(x1 + _ROI[0] * CELL),
        int(y1 + _ROI[2] * CELL),
        int(x1 + _ROI[1] * CELL),
        int(y1 + _ROI[3] * CELL),
    )


def all_cells() -> list[tuple[int, int]]:
    """Every box slot, in reading order."""
    return [(r, c) for r in range(1, ROWS + 1) for c in range(1, COLS + 1)]


# ---------------------------------------------------------------------------
# Colour keying
# ---------------------------------------------------------------------------
def badge_mask(bgr: np.ndarray) -> np.ndarray:
    """Binary mask of badge-coloured pixels."""
    return cv2.inRange(cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV), BADGE_HSV_LO, BADGE_HSV_HI)


def white_mask(bgr: np.ndarray) -> np.ndarray:
    """Binary mask of near-white pixels."""
    return cv2.inRange(cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV), WHITE_HSV_LO, WHITE_HSV_HI)


# ---------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------
_template_cache: np.ndarray | None = None


def load_template() -> np.ndarray:
    """The badge's lime fill, as a uint8 mask, taken from the stored template.

    The template on disk is a real screenshot crop with everything outside the
    badge's white outline made transparent.  What gets correlated is the lime
    fill inside it, keyed out of those same screenshot pixels -- the frame is
    keyed the same way, so like is compared with like.

    Correlating the badge's *pixels* instead was tried and measured worse: the
    tile background runs a strong brightness gradient down the grid (59 at the
    top row, 129 at the bottom), and against a badge this small that gradient
    dominates.  Over 64 real frames, keying by colour and correlating the shape
    identified the badge set exactly on 64 of them; `find_all` on the raw pixels
    managed 59 per-cell and 53 across the whole panel, at any threshold.
    """
    global _template_cache
    if _template_cache is None:
        bgra = cv2.imread(TEMPLATE_PATH, cv2.IMREAD_UNCHANGED)
        if bgra is None:
            raise FileNotFoundError(
                f"badge template missing: {TEMPLATE_PATH}\n"
                "Regenerate it with make_template() against a frame that shows a badge."
            )
        if bgra.shape[2] != 4:
            raise ValueError(f"{TEMPLATE_PATH} has no alpha channel")
        _template_cache = cv2.bitwise_and(badge_mask(bgra[:, :, :3]), bgra[:, :, 3])
    return _template_cache


def badge_shape(win: np.ndarray) -> np.ndarray:
    """Mask of the whole badge glyph: everything inside its white outline.

    The badge is a lime thumbs-up traced by a white outline.  Finding it is two
    steps:

    1. grow outward from the lime fill through white pixels only, which picks up
       the outline while ignoring any pale sprite that merely shares the corner
       without touching the badge;
    2. fill that region's outer contour, so the pixels *inside* the outline are
       kept whatever colour they happen to be -- the shaded gap between thumb
       and fist, the anti-aliased edges.

    Without the second step the mask comes out speckled with holes wherever a
    pixel sits between the two colour bands, which is not the shape of the thing
    on screen.
    """
    lime = badge_mask(win)
    pale = white_mask(win)
    reachable = cv2.bitwise_or(lime, pale)
    kernel = np.ones((3, 3), np.uint8)

    grown = lime
    while True:
        bigger = cv2.bitwise_and(cv2.dilate(grown, kernel), reachable)
        if np.array_equal(bigger, grown):
            break
        grown = bigger

    solid = np.zeros_like(grown)
    contours, _ = cv2.findContours(grown, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(solid, contours, -1, 255, thickness=cv2.FILLED)
    return solid


def make_template(frame: np.ndarray, row: int, col: int, path: str = TEMPLATE_PATH) -> np.ndarray:
    """Cut the badge out of a real frame and save it as a BGRA template.

    The colour in the file is the screenshot's own, untouched.  Alpha is the
    badge shape from ``badge_shape``, so every pixel outside the lime fill and
    its white outline is transparent -- no tile background, no sprite, no
    padding -- and the file is cropped to that shape's bounding box.

    Pick a slot whose Pokemon has nothing white up against the badge, or the
    outline grows into the sprite.  ``reference_frames/box_full_badges.png`` at
    (4, 6) is a good one: the Golbat there is nowhere near white.
    """
    x1, y1, x2, y2 = badge_roi(row, col)
    win = capture.get_frame_region(x1, y1, x2, y2, frame=frame)
    shape = badge_shape(win)
    ys, xs = np.nonzero(shape)
    if len(ys) == 0:
        raise ValueError(f"no badge in slot ({row},{col})")
    box = (slice(ys.min(), ys.max() + 1), slice(xs.min(), xs.max() + 1))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cv2.imwrite(path, np.dstack([win[box], shape[box]]))
    global _template_cache
    _template_cache = shape[box]
    return _template_cache


# ---------------------------------------------------------------------------
# Badge detection
# ---------------------------------------------------------------------------
def _shape_score(roi_mask: np.ndarray) -> tuple[float, tuple[int, int] | None]:
    """Best correlation of the badge silhouette against a colour-keyed window.

    Returns (score, top-left of the best hit within roi_mask).
    """
    tpl = load_template()
    best, best_loc = -1.0, None
    for scale in BADGE_SCALES:
        h = max(8, int(tpl.shape[0] * scale))
        w = max(8, int(tpl.shape[1] * scale))
        scaled = cv2.resize(tpl, (w, h))
        if scaled.shape[0] >= roi_mask.shape[0] or scaled.shape[1] >= roi_mask.shape[1]:
            continue
        res = cv2.matchTemplate(roi_mask, scaled, cv2.TM_CCOEFF_NORMED)
        res = np.nan_to_num(res, nan=-1.0, posinf=-1.0, neginf=-1.0)
        _, score, _, loc = cv2.minMaxLoc(res)
        if score > best:
            best, best_loc = float(score), (int(loc[0]), int(loc[1]))
    return best, best_loc


def _outline_ratio(roi_bgr: np.ndarray, roi_mask: np.ndarray) -> float:
    """Fraction of the ring just outside the badge shape that is white.

    The badge is drawn with a white outline; a green Pokemon body is not.  This
    is the check that stops a lime-green sprite in the corner of a tile from
    passing as a badge, and it is deliberately independent of the shape match.
    """
    if not roi_mask.any():
        return 0.0
    filled = (roi_mask > 0).astype(np.uint8)
    ring = cv2.dilate(filled, np.ones((7, 7), np.uint8)) - filled
    if ring.sum() == 0:
        return 0.0
    white = white_mask(roi_bgr) > 0
    return float(np.count_nonzero(white & (ring > 0))) / float(ring.sum())


def badge_at(frame: np.ndarray, row: int, col: int) -> tuple[bool, float, float]:
    """Is the thumbs-up badge on this slot?  Returns (present, score, outline)."""
    x1, y1, x2, y2 = badge_roi(row, col)
    roi = capture.get_frame_region(x1, y1, x2, y2, frame=frame)
    mask = badge_mask(roi)
    score, _ = _shape_score(mask)
    outline = _outline_ratio(roi, mask)
    return (score >= BADGE_SCORE_MIN and outline >= OUTLINE_RATIO_MIN, score, outline)


def scan_page(frame: np.ndarray) -> set[tuple[int, int]]:
    """Box slots carrying the badge.

    Read this ONCE per box page, with the cursor parked on the party, and then
    commit to the result: the badges do not refresh after a trade, so re-reading
    mid-page would hand back stale answers.
    """
    return {cell for cell in all_cells() if badge_at(frame, *cell)[0]}


# ---------------------------------------------------------------------------
# Cursor
# ---------------------------------------------------------------------------
def _tile_brightness(frame: np.ndarray, row: int, col: int) -> float:
    """Mean brightness of a narrow vertical strip inside the tile's left edge.

    Sampling the tile's *chrome* rather than its contents is what makes this
    reliable.  A strip across the top of the tile looks like the obvious choice
    and is not: party tiles are taller than box tiles and their sprites poke
    into the top band, so a selected party slot could read darker (143) than an
    unselected one holding a pale sprite (168).  The left edge is background on
    every tile in both rows, because the sprites are centred.

    Measured against a known 17-step cursor walk: the left strip identifies the
    selected tile 17/17 times with a worst-case margin of 82 brightness levels;
    the top band managed 15/17 with a worst case of 20.
    """
    x1, y1, _, y2 = tile_box(row, col)
    height = y2 - y1
    band = capture.get_frame_region(
        x1 + 8, int(y1 + 0.05 * height), x1 + 34, int(y1 + 0.95 * height), frame=frame
    )
    return float(band.mean())


def find_cursor(frame: np.ndarray) -> tuple[int, int] | None:
    """The selected slot, or None if nothing stands out.

    The selected tile turns near-white while every other tile stays dark, so the
    brightest tile wins.  This is a comparison, not a colour match, which keeps
    it working if the capture card shifts the palette -- but it does require a
    clear winner, hence CURSOR_MARGIN_MIN.
    """
    cells = [(0, c) for c in range(1, COLS + 1)] + all_cells()
    scored = sorted(
        ((_tile_brightness(frame, r, c), (r, c)) for r, c in cells), reverse=True
    )
    if len(scored) < 2 or scored[0][0] - scored[1][0] < CURSOR_MARGIN_MIN:
        return None
    return scored[0][1]


# ---------------------------------------------------------------------------
# Empty box
# ---------------------------------------------------------------------------
def page_is_empty(frame: np.ndarray) -> bool:
    """True when the visible box holds no Pokemon at all.

    This -- not the absence of badges -- is the stop condition for the filler: a
    full box can legitimately show no badges.
    """
    return all(slot_is_empty(frame, *cell) for cell in all_cells())


def slot_is_empty(frame: np.ndarray, row: int, col: int) -> bool:
    """True when the slot holds no Pokemon.

    An occupied tile carries a sprite, which makes its interior far more varied
    than the flat wash of an empty tile, so colour spread separates the two.
    """
    x1, y1, x2, y2 = tile_box(row, col)
    inner = capture.get_frame_region(
        x1 + int(0.12 * CELL), y1 + int(0.12 * CELL),
        x2 - int(0.12 * CELL), y2 - int(0.12 * CELL),
        frame=frame,
    )
    return float(inner.reshape(-1, 3).std(axis=0).mean()) < EMPTY_STD_MAX


EMPTY_STD_MAX = 12.0
# Measured over all 32 boxes: an occupied tile never scored below 18.6 and an
# empty one never above 8.1, so the threshold sits in a wide gap.


# ---------------------------------------------------------------------------
# Screen state
# ---------------------------------------------------------------------------
_PANEL = (144, 756, 1656, 2016)   # the box grid, in 4K coords
FLASH_MEAN_MIN = 180.0

# The "Search Boxes / View All Boxes / Cancel" hint bar along the bottom.  It is
# static chrome, so on the box screen it reads one exact value regardless of
# which box is open or whether it holds anything.
_HINT_BAR = (2560, 2040, 3720, 2120)
HINT_BAR_MEAN = 105.4
HINT_BAR_TOLERANCE = 1.5


def on_box_screen(frame: np.ndarray) -> bool:
    """True while the LINK TRADE box screen is up.

    Two independent signals, either of which is enough:

    * the hint bar along the bottom, which measured a single exact value (105.4)
      across all 72 box-screen frames captured and matched only 2 of the 80
      frames recorded during a trade;
    * a visible cursor.

    Neither alone is sufficient.  The hint bar is an absolute brightness and so
    would drift if the capture gain changed; the cursor vanishes on an empty box,
    where the selected tile has no sprites to outshine and the margin collapses
    -- and an empty box is exactly the page this script must recognise to stop.

    Callers waiting for the box screen to come *back* should require several
    consecutive hits: one mid-trade frame did briefly show it.
    """
    bar = float(capture.get_frame_region(*_HINT_BAR, frame=frame).mean())
    return abs(bar - HINT_BAR_MEAN) <= HINT_BAR_TOLERANCE or find_cursor(frame) is not None


def is_trade_flash(frame: np.ndarray) -> bool:
    """True on the white flash that opens the trade animation.

    Once this fires the trade is committed and A must stop being pressed until
    the box screen comes back.  The grid area sits at 103-123 mean brightness on
    the ordinary screens and shoots to 210-254 for the flash, so the threshold
    has a wide margin either side.
    """
    return float(capture.get_frame_region(*_PANEL, frame=frame).mean()) >= FLASH_MEAN_MIN
