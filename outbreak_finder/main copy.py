import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import time
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

from switch import (Fore, NotificationResult, Style, TelegramAction,
                    TelegramNotifier, _run, capture, click, connect,
                    controller, hold, hold_for, launch, notifier, ocr, release,
                    set_left_stick, set_right_stick, settings)

canvas_h = 2500
canvas_w = 2500
canvas = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)
weight = np.zeros((canvas_h, canvas_w), dtype=np.float32)

scales = [1.0]

def load_templates(folder: str):
    templates = []

    for path in Path(folder).glob("*"):
        templates.extend(load_multiscale_template_with_alpha(str(path), scales))

    return templates

from pathlib import Path

import cv2
import numpy as np


def load_multiscale_template_with_alpha(path: str, scales):
    base = load_template_with_alpha(path)

    templates = []
    for scale in scales:
        gray = cv2.resize(
            base["gray"],
            None,
            fx=scale,
            fy=scale,
            interpolation=cv2.INTER_LINEAR
        )
        mask = cv2.resize(
            base["mask"],
            None,
            fx=scale,
            fy=scale,
            interpolation=cv2.INTER_NEAREST
        )

        h, w = gray.shape[:2]
        if h < 8 or w < 8:
            continue

        templates.append({
            "name": Path(path).stem,
            "scale": scale,
            "gray": gray,
            "mask": mask,
            "w": w,
            "h": h,
        })

    return templates


def load_template_with_alpha(path: str):
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError(f"Could not load template: {path}")

    if img.shape[2] != 4:
        raise ValueError("Template must be a PNG with alpha channel")

    bgr = img[:, :, :3]
    alpha = img[:, :, 3]

    # Binary mask from alpha
    mask = (alpha > 0).astype(np.uint8) * 255

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)

    return {
        "bgr": bgr,
        "gray": gray,
        "mask": mask,
        "w": gray.shape[1],
        "h": gray.shape[0],
    }


def start():
    templates = load_templates(os.path.join(os.path.dirname(__file__), "find"))
    print([a["name"] for a in templates])
    while True:
        canvas = pan_map()
        matches = find_template_matches(canvas, templates)
        matches = nms_per_name(matches, iou_threshold=0.25)

        for m in matches:
            print(m["name"], m["score"], m["x"], m["y"])
            canvas = draw_matches(canvas, matches)

        print(matches)



        cv2.imwrite("map_pan_result.png", canvas)


        #INSERTED NOTIFIER HERE
        notifier.send_and_wait(
            text="Ready to pan the map?",
            console_prompt="Press Enter to start panning the map...",
            actions=[TelegramAction("Start", "start")],
        )
        # input("Press Enter to start panning the map...")

def draw_matches(canvas, matches):
    vis = canvas.copy()

    for m in matches:
        x, y, w, h = m["x"], m["y"], m["w"], m["h"]
        cv2.rectangle(vis, (x, y), (x + w, y + h), (0, 255, 0), 2)
        cv2.putText(
            vis,
            f'{m["name"]} {m["scale"]:.2f} {m["score"]:.2f}',
            (x, max(20, y - 5)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 255, 0),
            1,
            cv2.LINE_AA,
        )

    return vis


def preprocess_canvas(canvas: np.ndarray):
    gray = cv2.cvtColor(canvas, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    return gray


def find_template_matches(canvas: np.ndarray, templates, threshold=0.9):
    canvas_gray = cv2.cvtColor(canvas, cv2.COLOR_BGR2GRAY)
    canvas_gray = cv2.GaussianBlur(canvas_gray, (3, 3), 0)

    H, W = canvas_gray.shape[:2]
    matches = []

    for tpl in templates:
        th, tw = tpl["gray"].shape[:2]
        if th > H or tw > W:
            continue

        result = cv2.matchTemplate(
            canvas_gray,
            tpl["gray"],
            cv2.TM_CCORR_NORMED,
            mask=tpl["mask"]
        )

        ys, xs = np.where(result >= threshold)
        for x, y in zip(xs, ys):
            print(f"Found match for {tpl['name']} at ({x}, {y}) with score {result[y, x]:.4f}")
            matches.append({
                "name": tpl["name"],
                "scale": tpl["scale"],
                "x": int(x),
                "y": int(y),
                "w": tpl["w"],
                "h": tpl["h"],
                "score": float(result[y, x]),
            })

    return matches


def nms(matches, iou_threshold=0.3):
    if not matches:
        return []

    boxes = []
    scores = []

    for m in matches:
        x1 = m["x"]
        y1 = m["y"]
        x2 = x1 + m["w"]
        y2 = y1 + m["h"]
        boxes.append([x1, y1, x2, y2])
        scores.append(m["score"])

    boxes = np.array(boxes, dtype=np.float32)
    scores = np.array(scores, dtype=np.float32)

    order = scores.argsort()[::-1]
    keep = []

    while order.size > 0:
        i = order[0]
        keep.append(i)

        xx1 = np.maximum(boxes[i, 0], boxes[order[1:], 0])
        yy1 = np.maximum(boxes[i, 1], boxes[order[1:], 1])
        xx2 = np.minimum(boxes[i, 2], boxes[order[1:], 2])
        yy2 = np.minimum(boxes[i, 3], boxes[order[1:], 3])

        inter_w = np.maximum(0, xx2 - xx1)
        inter_h = np.maximum(0, yy2 - yy1)
        inter = inter_w * inter_h

        area_i = (boxes[i, 2] - boxes[i, 0]) * (boxes[i, 3] - boxes[i, 1])
        area_o = (boxes[order[1:], 2] - boxes[order[1:], 0]) * (
            boxes[order[1:], 3] - boxes[order[1:], 1]
        )

        iou = inter / (area_i + area_o - inter + 1e-6)
        inds = np.where(iou <= iou_threshold)[0]
        order = order[inds + 1]

    return [matches[i] for i in keep]


def nms_per_name(matches, iou_threshold=0.3):
    grouped = defaultdict(list)
    for m in matches:
        grouped[m["name"]].append(m)

    final = []
    for name, group in grouped.items():
        final.extend(nms(group, iou_threshold=iou_threshold))
    return final


def pan_map():
    global canvas, weight, current_x, current_y
    global prev_frame
    canvas = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)
    weight = np.zeros((canvas_h, canvas_w), dtype=np.float32)
    origin_x = canvas_w / 2
    origin_y = canvas_h / 2
    current_x = origin_x - (1672 - 387) / 2
    current_y = origin_y - (865 - 324) / 2
    prev_frame = None
    click("R", 1)
    click("L", 0.6)

    capture_direction("w", 1)
    capture_direction("z", 0.8)
    capture_direction("s", 1.3)
    capture_direction("d", 1)
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
    frame = capture.get_latest_frame()[324:865, 387:1672]  # type: ignore

    if prev_frame is not None:
        dx, dy, response = estimate_shift(prev_frame, frame)
        print(f"Estimated shift: dx={dx:.2f}, dy={dy:.2f}, response={response:.4f}")
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

    # Convert to grayscale
    prev_gray = cv2.cvtColor(prev_frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    # Convert to float32
    prev_f = np.float32(prev_gray)
    gray_f = np.float32(gray)

    # Estimate shift
    shift, response = cv2.phaseCorrelate(prev_f, gray_f)  # type: ignore
    dx, dy = shift

    return -dx, -dy, response


import numpy as np


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

    # Per-pixel RGB distance
    diff = np.linalg.norm(patch - canvas_patch, axis=2)

    # If pixel has never been seen, always accept it
    unseen = weight_patch == 0

    # If already seen, only accept if similar
    consistent = diff < diff_threshold

    accept = unseen | consistent

    # Update accepted pixels with running average
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
    launch(start)
