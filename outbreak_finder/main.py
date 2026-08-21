import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import re
import shutil
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from switch import *

canvas_h = 3500
canvas_w = 3500
canvas = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)
weight = np.zeros((canvas_h, canvas_w), dtype=np.float32)

# Template is 256px; icons on the 2500px canvas are ~60-100px → optimize for scale 0.40-0.65
# Check in order of likelihood: most common sizes first (early exit will save time)
FIND_SCALES = [0.50*1.3, 0.55*1.3, 0.45*1.3, 0.60*1.3, 0.65*1.3]  # ~65-110px, most common first
FIND_THRESHOLD = 0.45

m = 46

DEBUG_REGIONS = [
    ("minute", 1554, 85, 1614, 142, (0, 255, 0)),
]

SRC_ICONS_DIR = Path(os.path.dirname(__file__)) / "src_icons"
FIND_DIR = Path(os.path.dirname(__file__)) / "find"


# ──────────────────────────────────────────────────────────────────────────────
# Setup helpers
# ──────────────────────────────────────────────────────────────────────────────

def _console_file_link(path: str, label: str) -> str:
    """Return an OSC 8 hyperlink (ctrl+click in terminal opens the file)."""
    abs_path = os.path.abspath(path)
    url = "file:///" + abs_path.replace("\\", "/")
    return f"\x1b]8;;{url}\x1b\\{label}\x1b]8;;\x1b\\"


def _parse_indexes(raw: str) -> list[str]:
    """Split user input on any combination of spaces, commas, or newlines."""
    return [tok.strip() for tok in re.split(r"[\s,]+", raw.strip()) if tok.strip()]


def ask_which_variant(idx: str, matches: list[Path]) -> Path:
    """When multiple icons share the same index, ask the user which to use."""
    print(f"\nMultiple icons found for index {idx}:")
    for i, m in enumerate(matches, 1):
        link = _console_file_link(str(m), m.name)
        print(f"  {i}. {link}")

    # Send each photo to Telegram with a numbered caption
    for i, m in enumerate(matches, 1):
        try:
            notifier.send_photo(image_path=str(m), caption=f"{i}. {m.name}")
        except Exception as exc:
            print(f"  [Telegram] Could not send {m.name}: {exc}")

    result = notifier.send_and_wait(
        text=f"Select variant for index {idx}:",
        actions=[TelegramAction(str(i), str(i)) for i in range(1, len(matches) + 1)],
        console_prompt=f"  Select (1-{len(matches)}): ",
        wait_for_text=True,
    )

    choice_str = (result.action_value or "1").strip()
    try:
        choice = int(choice_str)
        if 1 <= choice <= len(matches):
            return matches[choice - 1]
    except (ValueError, TypeError):
        pass

    print("  Invalid selection, defaulting to option 1.")
    return matches[0]


def setup_run() -> tuple[str,str]:
    """Interactive setup: pick map, pick Pokémon icons.

    Returns the map choice string ("kk" or "bb").
    Populates the find/ directory with the selected icons.
    """
    # ── 1. Map selection ──────────────────────────────────────────────────────
    print("\nSelect map:  1. Kitakami (kk)   2. Blueberry (bb)")
    map_result = notifier.send_and_wait(
        text="Qual mapa? / Which map?",
        actions=[
            TelegramAction("Kitakami", "kk"),
            TelegramAction("Blueberry", "bb"),
        ],
        console_prompt="  Map (kk / bb): ",
        wait_for_text=True,
    )
    raw_map = (map_result.action_value or "kk").strip().lower()
    map_choice = "bb" if raw_map in ("bb", "blueberry", "2") else "kk"
    print(f"  → {map_choice}")

    # ── 2. Pokémon index input ────────────────────────────────────────────────
    index_result = notifier.send_and_wait(
        text=(
            "Envie os índices dos Pokémon (separados por espaços, vírgulas ou enters):\n"
            "Send the Pokémon indexes (spaces, commas, or newlines):"
        ),
        wait_for_text=True,
        console_prompt="  Pokémon indexes: ",
    )
    indexes = _parse_indexes(index_result.action_value or "")

    if not indexes:
        return (map_choice, "manual")
        # notifier.send_message("No indexes provided — aborting setup.")
        # raise RuntimeError("No Pokémon indexes provided.")

    print(f"  Indexes: {indexes}")

    # ── 3. Resolve icons ──────────────────────────────────────────────────────
    selected: list[Path] = []

    for idx in indexes:
        padded = idx.zfill(4)
        matches = sorted(SRC_ICONS_DIR.glob(f"pm{padded}_*_big.png"))

        if not matches:
            msg = f"No icon found for index {idx} (pm{padded})"
            print(f"  ⚠ {msg}")
            notifier.send_message(msg)
        elif len(matches) == 1:
            selected.append(matches[0])
            print(f"  ✓ {matches[0].name}")
        else:
            chosen = ask_which_variant(idx, matches)
            selected.append(chosen)
            print(f"  ✓ {chosen.name}")

    if not selected:
        raise RuntimeError("No icons could be resolved.")

    # ── 4. Confirm selection ──────────────────────────────────────────────────
    print("\nSelected icons for confirmation:")
    for i, p in enumerate(selected, 1):
        link = _console_file_link(str(p), p.name)
        print(f"  {i}. {link}")

    # Send all selected icons in one Telegram message (chunked if > 10)
    captions = [p.name for p in selected]
    chunks = [selected[i:i + 10] for i in range(0, len(selected), 10)]
    cap_chunks = [captions[i:i + 10] for i in range(0, len(captions), 10)]
    for chunk, cap_chunk in zip(chunks, cap_chunks):
        try:
            notifier.send_media_group([str(p) for p in chunk], captions=cap_chunk)
        except Exception as exc:
            print(f"  [Telegram] Media group failed, sending individually: {exc}")
            for p, cap in zip(chunk, cap_chunk):
                try:
                    notifier.send_photo(image_path=str(p), caption=cap)
                except Exception as exc2:
                    print(f"  [Telegram] Could not send {p.name}: {exc2}")

    confirm = notifier.send_and_wait(
        text="Confirm this selection?",
        actions=[
            TelegramAction("Confirm ✓", "confirm"),
            TelegramAction("Cancel ✗", "cancel"),
        ],
        console_prompt="  Confirm? (Enter = yes, 'n' = cancel): ",
        wait_for_text=True,
    )
    raw_confirm = (confirm.action_value or "").strip().lower()
    if raw_confirm in ("cancel", "n", "no"):
        raise RuntimeError("Setup cancelled by user.")

    # ── 5. Populate find/ directory ───────────────────────────────────────────
    FIND_DIR.mkdir(exist_ok=True)
    for f in FIND_DIR.glob("*.png"):
        f.unlink()
    for p in selected:
        shutil.copy2(p, FIND_DIR / p.name)

    print(f"\n  Copied {len(selected)} icon(s) to find/")
    return (map_choice, "normal")


# ──────────────────────────────────────────────────────────────────────────────
# Main entry point
# ──────────────────────────────────────────────────────────────────────────────

def _start():
    print("HERE")
    (map_choice, mode) = setup_run()
    pan_map = pan_map_bb if map_choice == "bb" else pan_map_kitikami

    notifier.send_and_wait(
        text="Ready to start panning the map?",
        console_prompt="Press Enter when ready to start panning the map",
        actions=[TelegramAction("Start", "start")],
    )
    notifier.send_message("A iniciar outbreak_finder")

    while True:
        main_loop(pan_map, mode)


def main_loop(pan_map, mode):
    canvas = pan_map()
    cv2.imwrite("map_pan_initial.png", canvas)

    if mode == "manual":
        result = notifier.send_and_wait(
            frame=canvas,
            text="Continue (skip the current)?",
            console_prompt="Press Enter to start panning the map...",
            actions=[TelegramAction("Yes", "yes"), TelegramAction("No", "no")],
        )
        if(result.action_value == "no"):
            while True:
                input()
        else:
            change_time()
            return
        


    matches = find_all_pokemon(canvas)
    for match in matches:
        print(
            f'{match["name"]}  score={match["score"]:.3f}'
            f'  scale={match["scale"]:.1f}  bbox={match["bbox"]}'
        )

    result_img = draw_matches(canvas, matches)
    cv2.imwrite("map_pan_result.png", result_img)

    if len(matches) > 0:
        print(f"Found {len(matches)} matches, panning again to refine...")
        not_result = notifier.send_and_wait(
            text=f"Found {len(matches)} matches",
            frame=result_img,
            actions=[
                TelegramAction("Continuar", "skip"),
                TelegramAction("Parar", "continue"),
            ],
            console_prompt="Press Enter to pan the map again...",
        )
        if not_result.action_value == "continue":
            exit(0)
    else:
        print("No matches found, done. Repanning the map")

    change_time()


# ──────────────────────────────────────────────────────────────────────────────
# Time manipulation
# ──────────────────────────────────────────────────────────────────────────────

def get_current_minute():
    global m
    m = ocr.read_int(capture.get_latest_frame(), 3108, 170, 3228, 284, debug_name="minuto") or 0


def change_time():
    gotoDateSettings()
    updateTimeSettings()


def gotoDateSettings():
    click("H", 0.5)
    get_current_minute()
    click("2")
    hold_for("1", 0.65, 0.1)
    click("A", 1)
    hold_for("2", 1.5, 0.1)
    click("1")
    hold_for("2", 0.85, 0.1)
    click("A")
    hold_for("2", 0.4, 0.1)
    click("A")


def updateTimeSettings():
    global m
    if m <= 0:
        hold_for("1", 0.35)
        click("2", 0.1)
        click("1", 0.1)
        m = 60
    else:
        hold_for("1", 0.4)
    click("2", 0.1)
    click("1", 0.1)
    click("A", 0.5)
    click("H", 0.5, repeat=2)
    time.sleep(1.5)
    m -= 1


# ──────────────────────────────────────────────────────────────────────────────
# Icon matching
# ──────────────────────────────────────────────────────────────────────────────

def find_all_pokemon(canvas):
    """Find all pokemon icons in the canvas using multi-scale template matching."""
    all_matches = []
    for template_path in FIND_DIR.glob("*.png"):
        matches = find_pokemon(canvas, template_path)
        all_matches.extend(matches)
    return all_matches


def find_pokemon(canvas, template_path, threshold=FIND_THRESHOLD):
    """
    Find a single pokemon icon in the canvas at multiple scales.
    The template is first cropped to the tight bounding box of its opaque pixels
    so transparent padding doesn't dilute the match score.
    Matches inside the black border of the map are rejected.
    Returns a list of dicts with keys: name, bbox (x,y,w,h), scale, score.
    """
    template_bgra = cv2.imread(str(template_path), cv2.IMREAD_UNCHANGED)
    if template_bgra is None:
        return []

    name = Path(template_path).stem

    if template_bgra.shape[2] == 4:
        alpha_full = template_bgra[:, :, 3]
    else:
        alpha_full = np.ones(template_bgra.shape[:2], dtype=np.uint8) * 255

    # Crop to tight bounding box of opaque pixels
    ys, xs = np.where(alpha_full > 127)
    if len(ys) == 0:
        return []
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    template_bgr = template_bgra[y0:y1, x0:x1, :3]
    alpha = alpha_full[y0:y1, x0:x1]

    map_gray = cv2.cvtColor(canvas, cv2.COLOR_BGR2GRAY)
    _, valid_map = cv2.threshold(map_gray, 15, 255, cv2.THRESH_BINARY)

    best_per_scale = []

    for scale in FIND_SCALES:
        th = max(1, int(template_bgr.shape[0] * scale))
        tw = max(1, int(template_bgr.shape[1] * scale))
        if th < 20 or tw < 20:
            continue
        if th >= canvas.shape[0] or tw >= canvas.shape[1]:
            continue

        tpl_scaled = cv2.resize(template_bgr, (tw, th)).astype(np.float32)
        mask_scaled = cv2.resize(alpha, (tw, th))
        opaque = mask_scaled > 127

        for c in range(3):
            mean_val = float(tpl_scaled[:, :, c][opaque].mean())
            tpl_scaled[:, :, c] = np.where(opaque, tpl_scaled[:, :, c], mean_val)

        result = cv2.matchTemplate(
            canvas, tpl_scaled.astype(np.uint8), cv2.TM_CCOEFF_NORMED
        )
        _, max_val, _, max_loc = cv2.minMaxLoc(result)

        if max_val < threshold:
            continue

        x, y = max_loc
        region_valid = valid_map[y : y + th, x : x + tw]
        if region_valid.mean() < 64:
            continue

        best_per_scale.append(
            {
                "name": name,
                "bbox": (x, y, tw, th),
                "scale": scale,
                "score": float(max_val),
            }
        )

        if max_val >= 0.75:
            break

    if not best_per_scale:
        return []

    best = max(best_per_scale, key=lambda m: m["score"])
    return [best]


def draw_matches(canvas, matches):
    vis = canvas.copy()

    for match in matches:
        if match is None:
            continue
        x, y, w, h = match["bbox"]
        cv2.rectangle(vis, (x, y), (x + w, y + h), (0, 255, 0), 2)
        cv2.putText(
            vis,
            match["name"],
            (x, max(20, y - 5)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 255, 0),
            1,
            cv2.LINE_AA,
        )

    return vis


# ──────────────────────────────────────────────────────────────────────────────
# Map panning
# ──────────────────────────────────────────────────────────────────────────────

def pan_map_kitikami():
    global canvas, weight, current_x, current_y
    global prev_frame
    canvas = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)
    weight = np.zeros((canvas_h, canvas_w), dtype=np.float32)
    origin_x = canvas_w / 2
    origin_y = canvas_h / 2
    current_x = origin_x - int((3334 - 804) / (2 * capture.get_scale()))
    current_y = origin_y - int((1741 - 601) / (2 * capture.get_scale()))
    prev_frame = None
    click("R", 0.6)
    click("L", 0.6)

    _original_map = cv2.imread(
        os.path.join(os.path.dirname(__file__), "original_kk.png")
    )
    assert _original_map is not None, "original_kk.png not found"
    _orig_h, _orig_w = _original_map.shape[:2]
    _original_gray = cv2.cvtColor(_original_map, cv2.COLOR_BGR2GRAY)
    while True:
        frame = capture.get_frame_region(804, 601, 3334, 1747)  # type: ignore
        frame_resized = cv2.resize(frame, (_orig_w, _orig_h))
        template_matcher.fast_equals(frame_resized, _original_map)
        score = float(result[0, 0])
        print(f"Map check score: {score:.4f}")
        if score >= 0.5:
            break
        click("R", 2)

    capture_direction("w", 1)
    capture_direction("a", 0.6)
    capture_direction("s", 1.7)
    capture_direction("d", 1.1)
    capture_direction("w", 1.9)

    capture_estimate_blend()

    return canvas


def pan_map_bb():
    global canvas, weight, current_x, current_y
    global prev_frame
    canvas = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)
    weight = np.zeros((canvas_h, canvas_w), dtype=np.float32)
    origin_x = canvas_w / 2
    origin_y = canvas_h / 2
    current_x = origin_x - int((3334 - 804) / (2 * capture.get_scale()))
    current_y = origin_y - int((1741 - 601) / (2 * capture.get_scale()))
    prev_frame = None
    click("R", 0.6)
    click("L", 0.6)

    _original_map = cv2.imread(os.path.join(os.path.dirname(__file__), "original_bb.png"))
    assert _original_map is not None, "original.png not found"
    _orig_h, _orig_w = _original_map.shape[:2]
    _original_gray = cv2.cvtColor(_original_map, cv2.COLOR_BGR2GRAY)
    while True:
        frame = capture.get_frame_region(804, 601, 3334, 1747)  # type: ignore
        frame_resized = cv2.resize(frame, (_orig_w, _orig_h))
        frame_gray = cv2.cvtColor(frame_resized, cv2.COLOR_BGR2GRAY)
        result = cv2.matchTemplate(frame_gray, _original_gray, cv2.TM_CCOEFF_NORMED)
        score = float(result[0, 0])
        print(f"Map check score: {score:.4f}")
        if score >= 0.5:
            break
        click("R", 2)

    capture_direction("w", 1)
    capture_direction("z", 1)
    capture_direction("s", 1.3)
    capture_direction("d", 1.2)
    capture_direction("w", 1.9)

    capture_estimate_blend()

    return canvas


def capture_direction(direction, duration):
    hold(direction)
    start_time = time.time()
    while time.time() - start_time < duration:
        capture_estimate_blend()
    release(direction)


def capture_estimate_blend():
    global canvas, weight, current_x, current_y
    global prev_frame
    frame = capture.get_frame_region(804, 601, 3334, 1747) # type: ignore

    if prev_frame is not None:
        dx, dy, response = estimate_shift(prev_frame, frame)
        current_x += dx
        current_y += dy
        blend_into_canvas_consensus(
            canvas, weight, frame, int(current_x), int(current_y)
        )
    else:
        blend_into_canvas_consensus(
            canvas, weight, frame, int(current_x), int(current_y)
        )
    prev_frame = frame


def estimate_shift(prev_frame, frame):
    prev_gray = cv2.cvtColor(prev_frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    prev_f = np.float32(prev_gray)
    gray_f = np.float32(gray)
    shift, _response = cv2.phaseCorrelate(prev_f, gray_f)  # type: ignore
    dx, dy = shift
    return -dx, -dy, _response


def blend_into_canvas_consensus(canvas, weight, frame, x, y, diff_threshold=25.0):
    h, w = frame.shape[:2]

    x0 = max(0, x)
    y0 = max(0, y)
    x1 = min(canvas.shape[1], x + w)
    y1 = min(canvas.shape[0], y + h)

    if x0 >= x1 or y0 >= y1:
        return

    fx0 = x0 - x
    fy0 = y0 - y
    fx1 = fx0 + (x1 - x0)
    fy1 = fy0 + (y1 - y0)

    patch = frame[fy0:fy1, fx0:fx1].astype(np.float32)
    canvas_patch = canvas[y0:y1, x0:x1].astype(np.float32)
    weight_patch = weight[y0:y1, x0:x1]

    diff = np.linalg.norm(patch - canvas_patch, axis=2)

    unseen = weight_patch == 0
    consistent = diff < diff_threshold
    accept = unseen | consistent

    new_weight = weight_patch.copy()
    new_canvas = canvas_patch.copy()

    yy, xx = np.where(accept)
    if len(yy) > 0:
        old_w = weight_patch[yy, xx]
        new_w = old_w + 1.0
        new_canvas[yy, xx] = (
            canvas_patch[yy, xx] * old_w[:, None] + patch[yy, xx]
        ) / new_w[:, None]
        new_weight[yy, xx] = new_w

    canvas[y0:y1, x0:x1] = new_canvas.astype(np.uint8)
    weight[y0:y1, x0:x1] = new_weight


if __name__ == "__main__":
    launch(_start, debug_regions=DEBUG_REGIONS)
