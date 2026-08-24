# region Enums

from enum import Enum, IntEnum

import capture
import cv2
import ocr
import template_matcher
from controller import click
from globals import get_file_relative_path


class Mode(Enum):
    OFF = 0
    HOME_MENU = 1
    IN_GAME = 2
    IN_BATTLE = 3
    MENU = 4
    PICNIC = 5
    BAG = 6
    BOXES = 7
    CAMERA = 8
    SANDWICH_EFFECTS = 9
    SANDWICH_INGREDIENTS = 10
    SANDWICH_MAKING = 11
    OUTFIT = 12
    EMOJIS = 13
    TEXT_BOX = 14
    TEXT_BOX_CONTINUE = 15
    QUESTION_INPUT = 16
    MAP = 17
    POKE_PORTAL = 18


class MenuOption(IntEnum):
    ERROR = 0
    MONS = 1
    BAG = 2
    BOXES = 3
    PICNIC = 4
    PORTAL = 5
    OPTIONS = 6
    SAVE = 7
    DLC = 8
    MIRAIDON = 9


# IN_BATTLE = 3
# SANDWICH_MAKING = 11
# OUTFIT = 12
# EMOJIS = 13


# endregion


# region Raid battle detectors
#
# Vision detectors for the Tera Raid ATTACKER macro on Switch 2 (see
# tera-raid-farmer/). They follow the exact pattern of get_mode()'s detectors:
# a 4K crop (auto-scaled by capture) matched against a reference PNG. All
# coordinates are in 4K (3840x2160).
#
# CALIBRATED 2026-08-18 against real SV Tera Raid frames captured from the live
# 1080p Elgato feed (tera-raid-farmer/calibrate.py). Each detector's crop rect,
# reference PNG and threshold were validated by running template_matcher against
# positive frames (the target screen) and negative frames (other screens); the
# validation scores are noted per detector. Templates were cut at 1080p native,
# and the live feed is 1080p, so scales=(1,) is exact.

_BATTLE_DETECTORS_CALIBRATED = True  # calibrated 2026-08-18 from live raid frames


def _battle_template(region_4k, png_name, threshold, scales=(1,)) -> bool:
    """True if `png_name` matches inside the 4K `region_4k` (x1, y1, x2, y2) crop."""
    x1, y1, x2, y2 = region_4k
    return bool(
        template_matcher.find_all(
            capture.get_frame_region(x1, y1, x2, y2),
            get_file_relative_path(f"../reference_images/{png_name}"),
            threshold=threshold,
            scales=scales,
        )
    )


def is_command_menu() -> bool:
    """True when the raid command menu (Battle / Cheer / Run) is open with the
    cursor on the gold 'Battle' button — i.e. it is my turn. Signal to start/
    continue the attacker macro. Validated: POS 0.98-1.00, NEG 0.00."""
    if not _BATTLE_DETECTORS_CALIBRATED:
        return False
    return _battle_template((2800, 1480, 3840, 1860), "raid_command_battle.png", 0.85)


def is_move_select() -> bool:
    """True when the move list is open, anchored on the 'Y Move Info' prompt at
    bottom-centre (cursor-independent, distinct from the command menu's 'Check
    Status'). Validated: POS 0.996-1.00, NEG <=0.74."""
    if not _BATTLE_DETECTORS_CALIBRATED:
        return False
    return _battle_template((1560, 1950, 2300, 2120), "raid_move_info.png", 0.90)


def tera_available() -> bool:
    """True when the 'TERASTALLIZE! [R]' prompt is shown in the move-select screen
    (press R to Terastallize from there). Validated on one positive frame (1.0);
    NEEDS more live confirmation — the green glow animates so partial frames may
    score lower. A false negative only costs a retry next turn."""
    if not _BATTLE_DETECTORS_CALIBRATED:
        return False
    return _battle_template((2160, 1560, 2800, 2000), "raid_tera_prompt.png", 0.80)


# Gold selection-fill strips per move row (4K), right side of each move card, clear
# of the icon/name. A row's strip is ~#d3b013 ONLY when that move is highlighted —
# same technique as get_portal_option's #ffe300 fill. Calibrated 2026-08-19 from live
# 1080p frames: on a slot-1-highlighted frame slot 1 ≈ 0.82 and slots 2-4 = 0.00; off
# the move list all ≈ 0.00. (Only slot-1 positives were available live; the 4 rows are
# identical at a fixed ~220px 4K pitch. Confirm slots 2-4 live on the next pass.)
_MOVE_SLOT_REGIONS = {
    1: (3200, 1230, 3700, 1370),
    2: (3200, 1454, 3700, 1594),
    3: (3200, 1678, 3700, 1818),
    4: (3200, 1890, 3700, 2030),
}
_MOVE_HIGHLIGHT_HEX = "#d3b013"
_MOVE_HIGHLIGHT_TOL = 55
_MOVE_HIGHLIGHT_MIN_RATIO = 0.5


def get_move_slot(frame=None) -> int:
    """Which move row (1-4) is highlighted, from the gold (#d3b013) selection fill in
    each row's clean strip. Returns 0 when nothing is clearly highlighted — which also
    means the move list isn't open, so it doubles as a cursor-on-a-move signal.
    Mirrors get_portal_option. Only meaningful while the move list is open."""
    if not _BATTLE_DETECTORS_CALIBRATED:
        return 0
    if frame is None:
        frame = capture.get_latest_frame()
    best, best_ratio = 0, 0.0
    for slot, (x1, y1, x2, y2) in _MOVE_SLOT_REGIONS.items():
        ratio = capture.region_color_ratio(
            x1, y1, x2, y2, _MOVE_HIGHLIGHT_HEX, tolerance=_MOVE_HIGHLIGHT_TOL, frame=frame
        )
        if ratio > best_ratio:
            best_ratio, best = ratio, slot
    return best if best_ratio >= _MOVE_HIGHLIGHT_MIN_RATIO else 0


def battle_ended() -> bool:
    """True at the end of the raid: a clean return to the overworld (minimap back
    in the corner) OR the raid result screen (the 'Rewards' label). Signal to STOP
    the Drain Punch loop. NOTE: the caller must only trust this AFTER the battle
    has started (see wait_for_battle_start in attacker.py) — the minimap is also in
    the corner while standing at the den in the pre-battle lobby. Result-screen
    validation: POS 1.0, NEG 0.00."""
    if _is_minimap_in_corner():
        return True
    if not _BATTLE_DETECTORS_CALIBRATED:
        return False
    return _battle_template((200, 400, 840, 600), "raid_result_rewards.png", 0.85)


def battle_lost() -> bool:
    """True on defeat / timeout (KO, 'blown out of the raid', timer at zero).

    NOT YET CALIBRATED — the calibration raid was won, so no defeat frame was
    captured. Kept inert (safe): losing is rare and the macro's safety cap
    handles a stuck loop. Capture a defeat screen live to enable this detector."""
    return False


def _in_battle() -> bool:
    """True when a raid battle is asking for my input (command menu or move list)."""
    return is_command_menu() or is_move_select()


# endregion


def get_text_box():
    if capture.region_is_mostly_color(2856, 1518, 3458, 1526, "#f1edf5", 0.8, 10):
        return "input"
    if capture.region_is_mostly_color(
        982, 1584, 2854, 1936, "#0e1f28", 0.7, 1
    ) or capture.region_is_mostly_color(930, 1590, 2907, 1935, "#f4edf4", 0.1, 4):
        return "text_box"
    return "error"


def get_mode():
    if capture.region_is_mostly_color(
        0, 0, capture._4K_W, capture._4K_H, "#080608", 1, 1
    ):
        return Mode.OFF

    if get_text_box() != "error":
        text_box_mode = get_text_box()
        if text_box_mode == "input":
            return Mode.QUESTION_INPUT

        if template_matcher.find_all(
            capture.get_frame_region(2758, 1831, 2839, 1932),
            get_file_relative_path("../reference_images/text_box_white_continue.png"),
            threshold=0.94,
            scales=(1,),
        ):
            return Mode.TEXT_BOX_CONTINUE
        else:
            return Mode.TEXT_BOX

    elif template_matcher.find_all(
        capture.get_frame_region(522, 252, 699, 384),
        get_file_relative_path("../reference_images/map_icon.png"),
        threshold=0.94,
        scales=(1, 1.3, 2),
    ):
        return Mode.MAP

    elif (
        _similarity(
            ocr.read_field(capture.get_latest_frame(), 2676, 351, 3093, 444),
            "MAIN MENU",
        )
        > 0.8
    ):
        return Mode.MENU

    elif template_matcher.find_all(
        capture.get_frame_region(44, 12, 198, 140),
        get_file_relative_path("../reference_images/sandwich_ingredients.png"),
        threshold=0.94,
        scales=(1, 1.3, 2),
    ):
        return Mode.SANDWICH_INGREDIENTS

    elif template_matcher.find_all(
        capture.get_frame_region(1856, 244, 1950, 332),
        get_file_relative_path("../reference_images/b_button.png"),
        threshold=0.94,
        scales=(1, 1.3, 2),
    ):
        return Mode.SANDWICH_EFFECTS

    elif template_matcher.find_all(
        capture.get_frame_region(44, 18, 200, 144),
        get_file_relative_path("../reference_images/bag.png"),
        threshold=0.94,
        scales=(1, 1.3, 2),
    ):
        return Mode.BAG

    elif template_matcher.find_all(
        capture.get_frame_region(112, 254, 210, 338),
        get_file_relative_path("../reference_images/pokeball.png"),
        threshold=0.94,
        scales=(1, 1.3, 2),
    ):
        return Mode.BOXES

    elif template_matcher.find_all(
        capture.get_frame_region(1832, 1782, 2012, 1940),
        get_file_relative_path("../reference_images/camera.png"),
        threshold=0.94,
        scales=(1, 1.5, 2),
    ):
        return Mode.CAMERA
    elif (
        _similarity(
            ocr.read_field(
                capture.get_latest_frame(),
                216,
                1994,
                606,
                2072,
                invert=True,
                config="--psm 8",
                debug_name="10",
            ),
            "Pack up and go",
        )
        > 0.9
    ):
        return Mode.PICNIC

    elif (
        _similarity(
            ocr.read_field(
                capture.get_latest_frame(),
                2684,
                2000,
                3176,
                2082,
                invert=True,
                config="--psm 8",
                debug_name="11",
            ),
            "Close Software",
        )
        > 0.9
    ):
        return Mode.HOME_MENU
    elif get_portal_option(capture.get_latest_frame()) != PortalOption.ERROR:
        return Mode.POKE_PORTAL
    elif _is_minimap_in_corner():
        return Mode.IN_GAME
    elif _in_battle():
        return Mode.IN_BATTLE

    return "error"


_MENU_OPTION_REGIONS = {
    MenuOption.MONS:    (956,  318,  1196, 1850),
    MenuOption.BAG:     (2982, 498,  3633, 630),
    MenuOption.BOXES:   (3024, 657,  3627, 780),
    MenuOption.PICNIC:  (3057, 816,  3630, 951),
    MenuOption.PORTAL:  (3210, 978,  3638, 1094),
    MenuOption.OPTIONS: (3108, 1142, 3630, 1250),
    MenuOption.SAVE:    (3006, 1296, 3500, 1418),
    MenuOption.DLC:     (3420, 1821, 3522, 1923),
    MenuOption.MIRAIDON:(768,  1932, 1032, 2130),
}


def get_menu_option():
    if get_mode() != Mode.MENU:
        return MenuOption.ERROR

    frame = capture.get_latest_frame()
    best_option, best_ratio = MenuOption.ERROR, 0.0
    for option, (x1, y1, x2, y2) in _MENU_OPTION_REGIONS.items():
        ratio = capture.region_color_ratio(x1, y1, x2, y2, "#ffe300", tolerance=80, frame=frame)
        if ratio > best_ratio:
            best_ratio = ratio
            best_option = option
    return best_option


def goto_menu_option(option: MenuOption):
    while get_mode() != Mode.MENU:
        click("X", 2)
        if get_mode() != Mode.MENU:
            click("H", 2, 2)
    click("1")
    cur_option = get_menu_option()
    if(cur_option == MenuOption.ERROR):
        breakpoint()
    if option == MenuOption.MIRAIDON or option == MenuOption.MONS:
        click("3")
        return

    move = option - cur_option
    if move == 0:
        return

    if abs(move) > (MenuOption.DLC - MenuOption.BAG) / 2:
        move = int(
            -(move / abs(move)) * ((MenuOption.DLC - MenuOption.BAG) - abs(move) + 1)
        )

    click("2" if move >= 0 else "4", 0.2, repeat=abs(move))


# ─────────────────────────────────────────────────────────────────────────────
# Poké Portal submenu (dynamic navigation, mirrors get_menu_option/goto_menu_option)
# ─────────────────────────────────────────────────────────────────────────────


class PortalOption(IntEnum):
    ERROR = -1
    UNION_CIRCLE = 0
    TERA_RAID = 1
    LINK_TRADE = 2
    SURPRISE_TRADE = 3
    LINK_BATTLE = 4
    BATTLE_STADIUM = 5
    MYSTERY_GIFT = 6


# Clean gold-highlight strips per Poké Portal option (4K). Each strip is ~100%
# #ffe300 ONLY when that option is selected — calibrated 2026-08-19 from live 1080p
# frames (own ratio ~1.0, every other option ~0.0). For the two image tiles (Union
# Circle / Tera Raid Battle) it's the right half of the gold label-bar (clear of the
# text); for the five list rows it's the empty area right of the label.
_PORTAL_OPTION_REGIONS = {
    PortalOption.UNION_CIRCLE:   (680, 564, 1110, 630),
    PortalOption.TERA_RAID:      (680, 916, 1110, 990),
    PortalOption.LINK_TRADE:     (980, 1060, 1150, 1120),
    PortalOption.SURPRISE_TRADE: (980, 1200, 1150, 1260),
    PortalOption.LINK_BATTLE:    (980, 1340, 1150, 1400),
    PortalOption.BATTLE_STADIUM: (980, 1480, 1150, 1540),
    PortalOption.MYSTERY_GIFT:   (980, 1620, 1150, 1680),
}


def get_portal_option(frame=None):
    """Which Poké Portal option is highlighted, from the gold (#ffe300) selection
    fill in each option's clean strip. Only meaningful while the Poké Portal menu is
    open. Returns PortalOption.ERROR if nothing is clearly highlighted."""
    if frame is None:
        frame = capture.get_latest_frame()
    best, best_ratio = PortalOption.ERROR, 0.0
    for option, (x1, y1, x2, y2) in _PORTAL_OPTION_REGIONS.items():
        ratio = capture.region_color_ratio(x1, y1, x2, y2, "#ffe300", tolerance=80, frame=frame)
        if ratio > best_ratio:
            best_ratio, best = ratio, option
    return best if best_ratio >= 0.5 else PortalOption.ERROR


def goto_portal_option(option: PortalOption, settle: float = 0.25) -> bool:
    """Move the Poké Portal cursor to `option` dynamically: detect the current
    option and step by the exact delta (Down/Up). Mirrors goto_menu_option. Stepping
    by the exact delta is correct whether the list clamps or wraps at the ends.
    Returns True once the cursor is confirmed on `option`."""
    cur = get_portal_option()
    if cur == PortalOption.ERROR:
        return False
    move = int(option) - int(cur)
    if move != 0:
        click("2" if move > 0 else "4", settle, repeat=abs(move))
    return get_portal_option() == option


# region Utils

# The minimap is always in the bottom-right corner.
# Crop size and expected circle radius are tuned for 1920x1080.


def _is_minimap_in_corner() -> bool:
    """Return True if the minimap circle is visible in the bottom-right corner.

    Uses Hough circle detection on the corner crop — no template needed,
    rotation-invariant, and very fast on a small region.
    """
    crop = capture.get_frame_region(3183, 1491, 3777, 2094)

    _MINIMAP_CROP_SIZE = crop.shape[0]
    _MINIMAP_RADIUS_MIN = capture.get_scaled_int(270)
    _MINIMAP_RADIUS_MAX = capture.get_scaled_int(300)

    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    circles = cv2.HoughCircles(
        gray,
        cv2.HOUGH_GRADIENT,
        dp=1,
        minDist=_MINIMAP_CROP_SIZE,  # only one circle expected
        param1=100,  # Canny upper threshold
        param2=50,  # accumulator threshold (higher = fewer false positives)
        minRadius=_MINIMAP_RADIUS_MIN,
        maxRadius=_MINIMAP_RADIUS_MAX,
    )
    if circles is None:
        return False
    cx, cy, _ = circles[0][0]
    center_x, center_y = crop.shape[1] / 2, crop.shape[0] / 2
    max_offset = _MINIMAP_CROP_SIZE * 0.15
    return abs(cx - center_x) <= max_offset and abs(cy - center_y) <= max_offset


def _levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a

    previous = list(range(len(b) + 1))

    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            insert_cost = current[j - 1] + 1
            delete_cost = previous[j] + 1
            replace_cost = previous[j - 1] + (ca != cb)
            current.append(min(insert_cost, delete_cost, replace_cost))
        previous = current

    return previous[-1]


def _similarity(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    dist = _levenshtein(a.lower().strip("\n "), b.lower().strip("\n "))
    return 1 - dist / max(len(a), len(b))


# endregion
