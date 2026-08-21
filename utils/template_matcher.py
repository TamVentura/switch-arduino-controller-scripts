"""
template_matcher.py — multi-scale template matching with multiple-instance detection.

Finds every occurrence of a template image inside a larger scene image using
OpenCV's normalised cross-correlation, alpha-mask support, and non-maximum
suppression (NMS) to eliminate overlapping duplicate hits.

Public API
----------
find_all(scene, template_path, *, scales, threshold, nms_overlap) -> list[Match]
find_all_from_array(scene, template_bgra, name, *, scales, threshold, nms_overlap) -> list[Match]
find_all_rotated(scene, template_path, *, scales, angles, threshold, nms_overlap) -> list[Match]
find_all_rotated_from_array(scene, template_bgra, name, *, scales, angles, threshold, nms_overlap) -> list[Match]
get_circle(image) -> np.ndarray
fast_equals(a, b, *, max_diff, compare_size) -> bool

Each returned Match is a dict:
    {
        "name":  str,          # stem of template_path (or supplied name)
        "bbox":  (x, y, w, h), # top-left corner + size in scene pixels
        "scale": float,        # scale factor that produced this hit
        "score": float,        # TM_CCOEFF_NORMED value (0-1, higher = better)
        "center": (cx, cy),    # centre of the bounding box
        "angle": float,        # rotation angle (degrees) — only in rotated variants
    }
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import cv2
import numpy as np

# ---------------------------------------------------------------------------
# Defaults (mirror the values used in outbreak_finder)
# ---------------------------------------------------------------------------
DEFAULT_SCALES: tuple[float, ...] = (0.50, 0.55, 0.45, 0.60, 0.65, 1)
DEFAULT_THRESHOLD: float = 0.45
DEFAULT_NMS_OVERLAP: float = 0.3  # IoU threshold for suppressing duplicates
DEFAULT_ANGLES: tuple[float, ...] = tuple(range(0, 360, 10))  # every 10°


# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------
Match = dict  # keys: name, bbox, scale, score, center


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    """Intersection-over-Union of two (x, y, w, h) boxes."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(ax, bx)
    iy = max(ay, by)
    ix2 = min(ax + aw, bx + bw)
    iy2 = min(ay + ah, by + bh)
    inter = max(0, ix2 - ix) * max(0, iy2 - iy)
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def _nms(candidates: list[Match], iou_threshold: float) -> list[Match]:
    """Non-maximum suppression: keep the highest-scoring non-overlapping boxes."""
    if not candidates:
        return []
    candidates = sorted(candidates, key=lambda m: m["score"], reverse=True)
    kept: list[Match] = []
    for cand in candidates:
        if all(_iou(cand["bbox"], k["bbox"]) < iou_threshold for k in kept):
            kept.append(cand)
    return kept


def _load_template(template_path: str | Path) -> np.ndarray | None:
    """Read template as BGRA (returns None on failure)."""
    return cv2.imread(str(template_path), cv2.IMREAD_UNCHANGED)


def _crop_to_opaque(template_bgra: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """
    Crop a BGRA image to the tight bounding box of its opaque pixels.
    Returns (bgr_crop, alpha_crop).  For images without an alpha channel
    a fully-opaque mask is synthesised.
    """
    if template_bgra.ndim == 3 and template_bgra.shape[2] == 4:
        alpha_full = template_bgra[:, :, 3]
    else:
        alpha_full = np.full(template_bgra.shape[:2], 255, dtype=np.uint8)

    ys, xs = np.where(alpha_full > 127)
    if len(ys) == 0:
        # Fully transparent — return original (matching will fail harmlessly)
        return template_bgra[:, :, :3], alpha_full

    y0, y1 = int(ys.min()), int(ys.max()) + 1
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    bgr = template_bgra[y0:y1, x0:x1, :3]
    alpha = alpha_full[y0:y1, x0:x1]
    return bgr, alpha


def _scale_template(
    bgr: np.ndarray, alpha: np.ndarray, scale: float
) -> tuple[np.ndarray, np.ndarray] | None:
    """
    Resize template and its alpha mask to the given scale.
    Returns None when the scaled size is too small or larger than any
    reasonable scene.
    """
    th = max(1, int(bgr.shape[0] * scale))
    tw = max(1, int(bgr.shape[1] * scale))
    if th < 8 or tw < 8:
        return None
    tpl = cv2.resize(bgr, (tw, th)).astype(np.float32)
    msk = cv2.resize(alpha, (tw, th))
    opaque = msk > 127

    # Fill transparent pixels with the local channel mean so they don't
    # pollute the correlation score.
    for c in range(3):
        mean_val = float(tpl[:, :, c][opaque].mean()) if opaque.any() else 0.0
        tpl[:, :, c] = np.where(opaque, tpl[:, :, c], mean_val)

    return tpl.astype(np.uint8), msk


# ---------------------------------------------------------------------------
# Core detector
# ---------------------------------------------------------------------------


def _detect_all_at_scale(
    scene: np.ndarray,
    tpl: np.ndarray,
    msk: np.ndarray,
    name: str,
    scale: float,
    threshold: float,
    print_thresholds = False,
) -> list[Match]:
    """
    Return every match above `threshold` for a single (pre-scaled) template.
    Uses a local-maximum scan over the correlation map so multiple instances
    of the same icon at the same scale are all captured.
    """
    if tpl.shape[0] >= scene.shape[0] or tpl.shape[1] >= scene.shape[1]:
        return []

    result = cv2.matchTemplate(scene, tpl, cv2.TM_CCOEFF_NORMED)
    th_h, tw_h = tpl.shape[:2]

    if(print_thresholds):
        print("scale", scale, "max_threshold", result.max(), np.sort(result)[-6:].max())
    # Collect every position above the threshold
    locs = np.argwhere(result >= threshold)  # (row, col) pairs
    matches: list[Match] = []
    for row, col in locs:
        score = float(result[row, col])
        x, y = int(col), int(row)
        bbox = (x, y, tw_h, th_h)
        matches.append(
            {
                "name": name,
                "bbox": bbox,
                "scale": scale,
                "score": score,
                "center": (x + tw_h // 2, y + th_h // 2),
            }
        )
    return matches


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def find_all_from_array(
    scene: np.ndarray,
    template_bgra: np.ndarray,
    name: str = "template",
    *,
    scales: Sequence[float] = DEFAULT_SCALES,
    threshold: float = DEFAULT_THRESHOLD,
    nms_overlap: float = DEFAULT_NMS_OVERLAP,
    print_thresholds: bool = False,
) -> list[Match]:
    """
    Find all instances of `template_bgra` inside `scene`.

    Parameters
    ----------
    scene        : BGR image (H×W×3 uint8) to search in.
    template_bgra: template image loaded with IMREAD_UNCHANGED (may have alpha).
    name         : label stored in every returned Match dict.
    scales       : iterable of scale factors to try (template is resized by each).
    threshold    : minimum TM_CCOEFF_NORMED score to consider a hit.
    nms_overlap  : IoU threshold for non-maximum suppression (0 = no merging).

    Returns
    -------
    List of Match dicts, one per detected instance, sorted by descending score.
    """
    bgr, alpha = _crop_to_opaque(template_bgra)

    all_candidates: list[Match] = []
    for scale in scales:
        scaled = _scale_template(bgr, alpha, scale)
        if scaled is None:
            continue
        tpl, msk = scaled
        if tpl.shape[0] >= scene.shape[0] or tpl.shape[1] >= scene.shape[1]:
            continue
        hits = _detect_all_at_scale(scene, tpl, msk, name, scale, threshold, print_thresholds=print_thresholds)
        all_candidates.extend(hits)

    return _nms(all_candidates, nms_overlap)


def find_all_rotated_from_array(
    scene: np.ndarray,
    template_bgra: np.ndarray,
    name: str = "template",
    *,
    scales: Sequence[float] = DEFAULT_SCALES,
    angles: Sequence[float] = DEFAULT_ANGLES,
    threshold: float = DEFAULT_THRESHOLD,
    nms_overlap: float = DEFAULT_NMS_OVERLAP,
) -> list[Match]:
    """
    Find all instances of `template_bgra` inside `scene`, trying every
    combination of scale and rotation angle.

    Parameters
    ----------
    scene        : BGR image (H×W×3 uint8) to search in.
    template_bgra: template image loaded with IMREAD_UNCHANGED (may have alpha).
    name         : label stored in every returned Match dict.
    scales       : iterable of scale factors to try.
    angles       : iterable of rotation angles in degrees (0 = no rotation).
    threshold    : minimum TM_CCOEFF_NORMED score to consider a hit.
    nms_overlap  : IoU threshold for non-maximum suppression.

    Returns
    -------
    List of Match dicts, sorted by descending score.  Each dict has an extra
    ``"angle"`` key with the rotation (in degrees) that produced the best hit.
    """
    bgr, alpha = _crop_to_opaque(template_bgra)

    all_candidates: list[Match] = []
    for scale in scales:
        scaled = _scale_template(bgr, alpha, scale)
        if scaled is None:
            continue
        tpl_bgr, tpl_msk = scaled
        th, tw = tpl_bgr.shape[:2]

        for angle in angles:
            if angle % 360 == 0:
                rot_tpl, rot_msk = tpl_bgr, tpl_msk
            else:
                # Rotate around the centre, expanding the canvas so corners are kept.
                cx, cy = tw / 2, th / 2
                M = cv2.getRotationMatrix2D((cx, cy), angle, 1.0)
                cos_a = abs(M[0, 0])
                sin_a = abs(M[0, 1])
                new_w = int(th * sin_a + tw * cos_a)
                new_h = int(th * cos_a + tw * sin_a)
                M[0, 2] += new_w / 2 - cx
                M[1, 2] += new_h / 2 - cy
                rot_tpl = cv2.warpAffine(
                    tpl_bgr,
                    M,
                    (new_w, new_h),
                    flags=cv2.INTER_LINEAR,
                    borderMode=cv2.BORDER_REPLICATE,
                )
                rot_msk = cv2.warpAffine(
                    tpl_msk,
                    M,
                    (new_w, new_h),
                    flags=cv2.INTER_NEAREST,
                    borderValue=0,
                )

            if rot_tpl.shape[0] >= scene.shape[0] or rot_tpl.shape[1] >= scene.shape[1]:
                continue

            hits = _detect_all_at_scale(scene, rot_tpl, rot_msk, name, scale, threshold)
            for h in hits:
                h["angle"] = float(angle)
            all_candidates.extend(hits)

    result = _nms(all_candidates, nms_overlap)
    # Ensure "angle" key exists even on non-rotated callers mixing results
    for m in result:
        m.setdefault("angle", 0.0)
    return result


def find_all_rotated(
    scene: np.ndarray,
    template_path: str | Path,
    *,
    scales: Sequence[float] = DEFAULT_SCALES,
    angles: Sequence[float] = DEFAULT_ANGLES,
    threshold: float = DEFAULT_THRESHOLD,
    nms_overlap: float = DEFAULT_NMS_OVERLAP,
) -> list[Match]:
    """
    Find all instances of the template image file inside `scene`, trying every
    combination of scale and rotation angle.

    Parameters
    ----------
    scene         : BGR image (H×W×3 uint8) to search in.
    template_path : path to the template image (PNG with optional alpha supported).
    scales        : iterable of scale factors to try.
    angles        : iterable of rotation angles in degrees (0 = no rotation).
                    Default: 0, 10, 20, … 350 (every 10°, full circle).
    threshold     : minimum TM_CCOEFF_NORMED score (0–1).
    nms_overlap   : IoU threshold for duplicate suppression.

    Returns
    -------
    List of Match dicts, one per detected instance, sorted by descending score.
    Each dict has keys: name, bbox (x,y,w,h), scale, score, center (cx,cy), angle (degrees).

    Raises
    ------
    FileNotFoundError if the template path does not exist.
    ValueError        if the template image cannot be decoded.
    """
    path = Path(template_path)
    if not path.exists():
        raise FileNotFoundError(f"Template not found: {path}")

    template_bgra = _load_template(path)
    if template_bgra is None:
        raise ValueError(f"Could not decode template image: {path}")

    return find_all_rotated_from_array(
        scene,
        template_bgra,
        path.stem,
        scales=scales,
        angles=angles,
        threshold=threshold,
        nms_overlap=nms_overlap,
    )

def find_all(
    scene: np.ndarray,
    template_path: str | Path,
    *,
    scales: Sequence[float] = DEFAULT_SCALES,
    threshold: float = DEFAULT_THRESHOLD,
    nms_overlap: float = DEFAULT_NMS_OVERLAP,
    print_thresholds: bool = False,
) -> list[Match]:
    """
    Find all instances of the template image file inside `scene`.

    Parameters
    ----------
    scene         : BGR image (H×W×3 uint8) to search in.
    template_path : path to the template image (PNG with optional alpha supported).
    scales        : iterable of scale factors to try.
    threshold     : minimum TM_CCOEFF_NORMED score (0–1).
    nms_overlap   : IoU threshold for duplicate suppression.

    Returns
    -------
    List of Match dicts, one per detected instance, sorted by descending score.
    Each dict has keys: name, bbox (x,y,w,h), scale, score, center (cx,cy).

    Raises
    ------
    FileNotFoundError if the template path does not exist.
    ValueError        if the template image cannot be decoded.
    """
    path = Path(template_path)
    if not path.exists():
        raise FileNotFoundError(f"Template not found: {path}")

    template_bgra = _load_template(path)
    if template_bgra is None:
        raise ValueError(f"Could not decode template image: {path}")

    name = path.stem
    return find_all_from_array(
        scene,
        template_bgra,
        name,
        scales=scales,
        threshold=threshold,
        nms_overlap=nms_overlap,
        print_thresholds=print_thresholds,
    )


def get_circle(
    image: np.ndarray | str | Path,
    cx: int,
    cy: int,
    radius: int,
) -> np.ndarray:
    """
    Crop a circle at a specific location from an image.

    Pixels outside the circle are made transparent (alpha = 0).
    The returned array has the same dimensions as the input.

    Parameters
    ----------
    image  : BGR/BGRA numpy array, or a file path (str / Path).
    cx     : x coordinate of the circle centre (pixels).
    cy     : y coordinate of the circle centre (pixels).
    radius : radius of the circle (pixels).

    Returns
    -------
    BGRA numpy array cropped to the circle's bounding box (≤ 2r × 2r) with
    pixels outside the circle set to transparent.
    """
    if isinstance(image, (str, Path)):
        raw = cv2.imread(str(image), cv2.IMREAD_UNCHANGED)
        if raw is None:
            raise FileNotFoundError(f"Could not load image: {image}")
    else:
        raw = image

    h, w = raw.shape[:2]

    # Ensure we have a BGRA working copy
    if raw.ndim == 2:
        bgra = cv2.cvtColor(raw, cv2.COLOR_GRAY2BGRA)
    elif raw.shape[2] == 3:
        bgra = cv2.cvtColor(raw, cv2.COLOR_BGR2BGRA)
    else:
        bgra = raw.copy()

    # Clamp bounding box to image boundaries
    x0 = max(0, cx - radius)
    y0 = max(0, cy - radius)
    x1 = min(w, cx + radius)
    y1 = min(h, cy + radius)

    # Crop to bounding box first
    bgra = bgra[y0:y1, x0:x1]
    crop_h, crop_w = bgra.shape[:2]

    # Circle centre relative to the cropped region
    local_cx = cx - x0
    local_cy = cy - y0

    # Build circular mask over the cropped region
    mask = np.zeros((crop_h, crop_w), dtype=np.uint8)
    cv2.circle(mask, (local_cx, local_cy), radius, 255, thickness=-1)

    # Zero out alpha outside the circle
    bgra[:, :, 3] = np.where(mask == 255, bgra[:, :, 3], 0)

    return bgra


def fast_equals(
    a: np.ndarray | str | Path,
    b: np.ndarray | str | Path,
    *,
    max_diff: float = 10.0,
    compare_size: tuple[int, int] = (64, 64),
) -> bool:
    """
    Return True if two images are visually identical modulo encoding artifacts.

    Both images are downsampled to `compare_size` and compared channel-by-channel.
    If the mean absolute difference per channel is below `max_diff` (0–255 scale)
    the images are considered equal.

    Parameters
    ----------
    a, b         : BGR numpy arrays or file paths.
    max_diff     : maximum allowed mean-absolute-difference per channel (default 10).
                   Tune upward if your source has heavy JPEG compression.
    compare_size : (width, height) to resize both images to before comparing.
                   Smaller = faster; (64, 64) is usually sufficient.

    Returns
    -------
    bool
    """

    def _load(img: np.ndarray | str | Path) -> np.ndarray:
        if isinstance(img, (str, Path)):
            arr = cv2.imread(str(img), cv2.IMREAD_COLOR)
            if arr is None:
                raise FileNotFoundError(f"Could not load image: {img}")
            return arr
        # Convert to BGR if BGRA so channel counts match
        if img.ndim == 3 and img.shape[2] == 4:
            return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        return img

    fa = cv2.resize(_load(a), compare_size).astype(np.float32)
    fb = cv2.resize(_load(b), compare_size).astype(np.float32)

    if fa.shape != fb.shape:
        return False

    diff = np.abs(fa - fb)
    # Check mean diff per channel — a single noisy channel shouldn't cause a false negative
    return bool(diff.reshape(-1, fa.shape[2]).mean(axis=0).max() <= max_diff)


def draw_matches(scene: np.ndarray, matches: list[Match]) -> np.ndarray:
    """
    Return a copy of `scene` with bounding boxes and labels drawn for every
    match (mirrors the helper in outbreak_finder/main.py).
    """
    vis = scene.copy()
    for m in matches:
        if m is None:
            continue
        x, y, w, h = m["bbox"]
        cv2.rectangle(vis, (x, y), (x + w, y + h), (0, 255, 0), 2)
        cv2.putText(
            vis,
            f"{m['name']} {m['score']:.2f}",
            (x, max(20, y - 5)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 255, 0),
            1,
            cv2.LINE_AA,
        )
    return vis
