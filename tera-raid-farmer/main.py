"""
tera-raid-farmer/main.py - Joycon / Arduino Controller Automation for Switch 2 (Farmer).
Synchronized with Host Wrapper (Switch 1) API to join Tera Raids and collect Herba/Pokémon.
"""

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

# Ensure switch-arduino bootstrap is imported
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from switch import *

# Added imports for vision utilities and configuration
from utils.pokemon_sv import (
    get_mode,
    Mode,
    MenuOption,
    goto_menu_option,
    PortalOption,
    goto_portal_option,
    get_portal_option,
    get_text_box,
)
from colorama import Fore, Style

# Vision-driven Iron Hands attacker macro (Switch 2 is now the ATTACKER).
from attacker import AttackerConfig, run_attacker_macro, catch_pokemon, clear_rewards
# Event-driven link to the host over its /ws push (replaces all HTTP polling).
from host_link import HostLink

# ── Overlay debug regions for OpenCV capture card preview ────────────────────
_DEBUG_REGIONS = [
    ("raid_banner", 600, 200, 1320, 380, (0, 255, 0)),
    ("code_prompt", 750, 450, 1170, 630, (255, 128, 0)),
]

# ── Switch 2 Tera-Raid link-code keypad (3×4 NUMERIC grid) ───────────────────
# Grid position (row, col) of each digit key. Layout, verified live 2026-08-19:
#     1 2 3   ⌫(B)      row 0
#     4 5 6   ┌────┐    row 1
#     7 8 9   │ OK │    row 2   (OK spans rows 1-2, col 3; '+' presses it directly)
#       0     └────┘    row 3   ('0' is the lone row-3 cell, col 1)
# Navigation facts (see type_link_code_numeric):
#   Up clamps at row 0; Down clamps at the bottom → the '0' key (from any column);
#   Left/Right wrap horizontally; A enters the highlighted digit (cursor stays);
#   the keypad always opens with the cursor on '1'.
_KEYPAD_POS = {
    "1": (0, 0), "2": (0, 1), "3": (0, 2),
    "4": (1, 0), "5": (1, 1), "6": (1, 2),
    "7": (2, 0), "8": (2, 1), "9": (2, 2),
    "0": (3, 1),
}


def load_config() -> dict:
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "host_api_url": "http://127.0.0.1:8080",
        "rewards_mash_count": 25,
        "code_entry_attempts": 3,   # retries of the Link Code entry per lobby push
        "auto_catch": True,
        "catch_ball": "Timer Ball",  # pokemon mode: always catch with this ball
        # ── Iron Hands attacker macro (Switch 2 is now the ATTACKER) ──
        "belly_drum_slot": 1,       # move-slot of Belly Drum (turn 1, once)
        "drain_punch_slot": 2,      # move-slot of Drain Punch (turn 2+, loop)
        "use_tera": False,           # Terastallize (Fighting) from turn 2
        "tera_button": "R",         # controller token for Terastal
        "combat_max_turns": 50,     # safety cap against an infinite loop
        "turn_poll_timeout_sec": 30,   # max wait for "it's my turn" per turn
        "battle_start_timeout_sec": 200,  # max wait for the battle to begin
        # Delay (s) between button presses while navigating the Fight/move menus.
        # NEEDS LIVE CALIBRATION on the real Switch 2 capture feed.
        "move_select_delay_sec": 0.6,
    }


def signal_host_ready(base_url: str) -> bool:
    """Sends start_battle action signal to Switch 1 Host API."""
    url = f"{base_url.rstrip('/')}/api/action"
    payload = json.dumps({"action": "start_battle"}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "Switch2-ArduinoFarmer/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            return resp.status == 200
    except Exception:
        return False


def signal_caught(base_url: str) -> bool:
    """Best-effort: tell the Host (Switch 1) that the mon at the parked den was CAUGHT, so its
    den_seed is recorded and never searched again. The Host stamps den_seed+version from the live
    inject target — this ping carries no payload. Never raises."""
    url = f"{base_url.rstrip('/')}/api/caught"
    req = urllib.request.Request(
        url,
        data=b"{}",
        headers={"Content-Type": "application/json", "User-Agent": "Switch2-ArduinoFarmer/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            return resp.status == 200
    except Exception:
        return False


def type_link_code_numeric(code: str, step: float = 0.12) -> bool:
    """Type a numeric link code on the Switch 2 Tera-Raid keypad (3×4 grid) and
    confirm with '+' (OK), which also fires the search immediately.

    Returns True once OK has been pressed (the search is running). Returns False —
    WITHOUT pressing OK — when the keypad did not end up holding all 4 digits, which
    is what a mis-navigated keypress leaves behind: the game refuses OK on a short
    code and we would sit on the keypad forever. The keypad is left open so the
    caller can cancel out and type the code again.

    Verified live 2026-08-19: the keypad opens with the cursor on '1'; Up clamps at
    the top row; Down clamps at the bottom → the '0' key from any column; Left/Right
    wrap horizontally; 'A' enters the highlighted digit and leaves the cursor there;
    '+' presses OK (no need to navigate to the OK key) and starts the search.

    We track the cursor (row, col) from the known start and move by exact deltas —
    always staying within columns 0-2 / rows 0-2 for the digits so we never press
    past an edge (where Left/Right would wrap) — and special-case the lone '0' cell
    (reached by Down-to-bottom, left by Up back into the grid → '8')."""
    print(f"{Fore.YELLOW}[JOYCON]{Style.RESET_ALL} Typing numeric Link Code "
          f"[{Fore.GREEN}{code}{Style.RESET_ALL}] on the Switch 2 keypad...")

    # TEMP-CALIB: screenshot before every INDIVIDUAL keypress (each `repeat` is
    # split into single presses purely so each frame is captured — same presses,
    # same timing). Lets us see frame-by-frame whether a D-pad press moves one cell
    # or overshoots. Delete `c()` + _kp_* with the TEMP-CALIB block.
    _kp_reset()

    def c(token, sleep=step, repeat=1):
        for _ in range(repeat):
            _kp_shot(f"{ch}_{token}")
            if token in ("1", "2", "3", "4"):
                # Short D-pad tap: a normal click holds ~0.2s, long enough for the
                # keypad's auto-repeat to fire and skip a cell (double-step). A 50ms
                # tap moves exactly one cell (validated live 2026-08-24, same as the
                # ball carousel). A/+ stay as discrete clicks.
                hold_for(token, 0.05, sleep=sleep)
            else:
                click(token, sleep=sleep)

    cur_r, cur_c = 0, 0  # keypad always opens on '1'

    for ch in str(code):
        if ch not in _KEYPAD_POS:
            continue
        tr, tc = _KEYPAD_POS[ch]

        # Leaving the lone '0' cell first: step Up into the 3×3 grid (0 → 8).
        if (cur_r, cur_c) == (3, 1) and (tr, tc) != (3, 1):
            c("4")                          # Up
            cur_r, cur_c = 2, 1

        if (tr, tc) == (3, 1):
            # Target '0': align to column 1 (in a digit row), then Down to the
            # bottom (Down clamps on '0').
            if (cur_r, cur_c) != (3, 1):
                dc = 1 - cur_c
                if dc:
                    c("1" if dc > 0 else "3", repeat=abs(dc))
                c("2", repeat=3)            # Down → '0'
                cur_r, cur_c = 3, 1
        else:
            # Column first (stays in rows 0-2), then row — never enters row 3, and
            # each step moves toward the target so we never press past an edge.
            dc = tc - cur_c
            if dc:
                c("1" if dc > 0 else "3", repeat=abs(dc))
            dr = tr - cur_r
            if dr:
                c("2" if dr > 0 else "4", repeat=abs(dr))
            cur_r, cur_c = tr, tc

        c("A")                              # enter the digit; cursor stays

    time.sleep(0.2)

    # The OK button only lights up (cyan) at 4/4. If it is still idle we typed fewer
    # digits than we meant to, and pressing '+' would be silently refused — bail out
    # and let the caller retype instead of getting stuck here.
    if not link_code_ready():
        print(f"{Fore.YELLOW}[JOYCON]{Style.RESET_ALL} keypad did not take the whole "
              f"code [{code}] — not pressing OK (it would be refused)")
        _kp_shot("incomplete")
        return False

    _kp_shot("plus_OK")
    click("+", sleep=2.0)                   # OK — confirms the code AND searches
    _kp_shot("after_search")
    return True


# 4K gold strip that reads ~100% #ffe300 ONLY when the joined raid lobby is showing
# with "All ready to go!" highlighted. Calibrated live 2026-08-22: lobby=1.0, Poké
# Portal=0.0, Tera Raid Battle Search=0.0 — a clean few-pixels separator.
_READY_BAR_REGION = (1200, 620, 1780, 720)


def at_raid_lobby(frame=None) -> bool:
    """True when we have actually joined the host's raid lobby ('All ready to go!'
    highlighted). Used to time the ready-confirm press and to prove the join
    succeeded before telling the host to start."""
    try:
        return capture.region_color_ratio(
            *_READY_BAR_REGION, "#ffe300", tolerance=80, frame=frame) >= 0.6
    except Exception:
        return False


# ── Link Code keypad state, read off the OK button ───────────────────────────
# The keypad's OK button is a big flat block whose colour says whether the typed
# code is submittable. Calibrated live 2026-09-02 over 60+ frames of every screen
# this flow visits:
#   #3e586a (steel blue) → keypad open, code INCOMPLETE (fewer than 4 digits)
#   #2eace4 (cyan)       → keypad open, 4/4 digits in — OK will accept and search
#   neither              → the keypad is not on screen (≤ 0.05 on every other screen)
_KEYPAD_OK_REGION = (2740, 1400, 2990, 1850)
_KEYPAD_OK_IDLE = "#3e586a"
_KEYPAD_OK_READY = "#2eace4"


def _keypad_ok_ratio(hex_color: str, tolerance: float, frame=None) -> float:
    try:
        return capture.region_color_ratio(
            *_KEYPAD_OK_REGION, hex_color, tolerance=tolerance, frame=frame
        )
    except Exception:
        return 0.0


def link_code_ready(frame=None) -> bool:
    """True when the keypad is open with all 4 digits typed (OK lit cyan) — only
    then does pressing '+' actually fire the search. With fewer digits the game
    silently refuses OK and we stay on the keypad forever."""
    return _keypad_ok_ratio(_KEYPAD_OK_READY, 40, frame) >= 0.6


def at_link_code_keypad(frame=None) -> bool:
    """True while the Link Code keypad is on screen, complete code or not. Lets us
    tell 'the search is running' from 'OK was refused and we are still typing'."""
    return (
        link_code_ready(frame)
        or _keypad_ok_ratio(_KEYPAD_OK_IDLE, 20, frame) >= 0.6
    )


def _wait_search_outcome(timeout: float = 20.0) -> str:
    """Poll the screen after the Link Code search was fired and say how it went:

      'lobby'   — we are in the host's lobby ('All ready to go!' showing)
      'no_raid' — the game answered with a text box ("No raid was found!"), which is
                  what a wrong or stale code gets
      'keypad'  — the keypad is still up, so OK never took the code
      'timeout' — none of the above within `timeout`

    Lobby is checked first: it is the only outcome that means we are done.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            frame = capture.get_latest_frame()
        except Exception:
            time.sleep(0.4)
            continue
        if at_raid_lobby(frame):
            return "lobby"
        if at_link_code_keypad(frame):
            return "keypad"
        if get_text_box() != "error":
            return "no_raid"
        time.sleep(0.4)
    return "timeout"


def _ensure_in_poke_portal(max_tries: int = 12) -> bool:
    """Get into the Poké Portal from wherever the game currently is, WITHOUT ever
    pressing HOME (except to leave the Switch HOME menu). We may already be in the
    portal — or stranded on a portal sub-screen (e.g. the Tera Raid Battle Search
    card) from a previous attempt — in which case opening the main menu is wrong.

    Decision each iteration (get_portal_option != ERROR means the portal is open):
      • portal already open          → done
      • Switch HOME menu             → H (back into the game)
      • overworld / main menu        → open the menu, highlight Portal, A to open it
      • anything else / 'error'      → B, backing out one level toward the overworld
    """
    stuck = 0
    for _ in range(max_tries):
        time.sleep(0.4)                        # let the screen settle before reading vision
        if get_portal_option() != PortalOption.ERROR:
            return True
        mode = get_mode()
        print(f"{Fore.LIGHTBLUE_EX}[VISION]{Style.RESET_ALL} Game Mode: {mode}")
        if mode in (Mode.IN_GAME, Mode.MENU):
            # Safe to open the main menu here: goto_menu_option only reaches its HOME
            # fallback when X fails to open the menu, which won't happen from these.
            stuck = 0
            goto_menu_option(MenuOption.PORTAL)
            click("A", sleep=3.0)             # open the Poké Portal
        elif mode == Mode.HOME_MENU:
            # This is the "Close Software?" prompt — cancel with B. NEVER press A here
            # (A would close the game).
            stuck = 0
            click("B", sleep=1.5)
        else:
            # Unknown/dim/transition, a portal sub-screen (Tera Raid Battle Search,
            # keypad, a lingering lobby), or the plain Switch HOME screen. Wake +
            # settle and re-check the portal before acting (a dim portal reads as
            # ERROR); only then back out a level. If we stay unresolved, we may be
            # stranded on the Switch HOME menu, where H returns to the game.
            stuck += 1
            wake_screen()
            time.sleep(0.4)
            if get_portal_option() != PortalOption.ERROR:
                return True
            if stuck >= 3:
                click("H", sleep=2.0)          # escape a possible Switch HOME menu
                stuck = 0
            else:
                click("B", sleep=1.0)          # back out one level toward the overworld
    return False


def enter_tera_raid_lobby(code: str, attempts: int = 3) -> bool:
    """Join the host's Tera Raid lobby on Switch 2: (reach) Poké Portal → Tera Raid
    Battle → Link Code keypad → type `code` → search → confirm 'All ready to go!'.

    Portal-aware: if we are already in the Poké Portal (or one of its sub-screens)
    we do NOT go back through the main menu — see _ensure_in_poke_portal.

    The code entry itself is retried up to `attempts` times, because both ways it
    can fail land us back on the Tera Raid Battle Search screen for free:
      • the game answers "No raid was found!" (wrong/stale code) → 'A' dismisses it
      • the keypad refuses OK because fewer than 4 digits went in → 'X' cancels it
    Only the walk to the search screen happens once; everything from the keypad on
    is what repeats.

    Returns True only once we are confirmed inside the host's lobby (via at_raid_lobby)
    and have pressed 'All ready to go!'. Returns False if the join never lands, so the
    caller can skip signalling the host instead of triggering a solo host battle."""
    print(f"{Fore.CYAN}[FARMER]{Style.RESET_ALL} Reaching Poke Portal -> Tera Raid Battle...")

    # 1. Get into the Poké Portal from any starting screen (never presses HOME).
    if not _ensure_in_poke_portal():
        print(f"{Fore.YELLOW}[WARN]{Style.RESET_ALL} could not confirm the Poké Portal is open; "
              "continuing best-effort")

    # 2. Poké Portal → Tera Raid Battle (dynamic).
    if not goto_portal_option(PortalOption.TERA_RAID):
        print(f"{Fore.YELLOW}[WARN]{Style.RESET_ALL} portal option not confirmed; "
              "assuming cursor is on Tera Raid Battle")
    click("A", sleep=4.0)                    # → Tera Raid Battle Search (Link Code card)

    attempts = max(1, int(attempts))
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            print(f"{Fore.CYAN}[FARMER]{Style.RESET_ALL} typing the code again "
                  f"(attempt {attempt}/{attempts})...")

        # 3. Open the Link Code keypad ('Link Code' card is pre-selected). A previous
        #    attempt always cancels/dismisses back to this screen, so the same press
        #    works every time round — unless we are somehow still on the keypad.
        if not at_link_code_keypad():
            click("A", sleep=1.5)

        # 4. Type the code — '+' also fires the search. False means the keypad never
        #    reached 4/4 and OK was never pressed: cancel out and retype from a fresh
        #    keypad (whose cursor is back on '1', as the typing routine assumes).
        if not type_link_code_numeric(code):
            click("X", sleep=1.5)            # Cancel → Tera Raid Battle Search
            continue

        # 5. Wait for the join to actually land (the lobby screen appears) before
        #    pressing 'All ready to go!'. Pressing A blindly during the search/join
        #    transition misses the lobby and leaves us un-readied.
        outcome = _wait_search_outcome(20.0)

        if outcome == "lobby":
            print(f"{Fore.LIGHTGREEN_EX}[FARMER]{Style.RESET_ALL} Lobby joined — "
                  "confirming 'All ready to go!'")
            click("A", sleep=1.5)
            return True

        if outcome == "no_raid":
            print(f"{Fore.YELLOW}[WARN]{Style.RESET_ALL} the game answered 'No raid was "
                  f"found!' for [{code}]")
            click("A", sleep=1.5)            # dismiss → Tera Raid Battle Search
            continue

        if outcome == "keypad":
            print(f"{Fore.YELLOW}[WARN]{Style.RESET_ALL} OK did not take the code — "
                  "still sitting on the keypad")
            click("X", sleep=1.5)            # Cancel → Tera Raid Battle Search
            continue

        # 'timeout': the search neither joined nor errored, so we have no idea what is
        # on screen — retrying blind would press A/X somewhere unknown. Bail out and
        # let the caller wait for the host to re-host.
        print(f"{Fore.YELLOW}[WARN]{Style.RESET_ALL} raid lobby not detected after "
              "entering the code, and no error box either — join failed")
        return False

    print(f"{Fore.YELLOW}[WARN]{Style.RESET_ALL} gave up on [{code}] after {attempts} "
          "code-entry attempt(s) — join failed")
    return False


def handle_battle_and_rewards(
    attacker_cfg: AttackerConfig,
    host_link: HostLink,
    rewards_mash: int = 25,
    auto_catch: bool = True,
    host_api=None,
) -> None:
    """
    Vision-driven Iron Hands attacker macro + reward collection for the Switch 2
    farmer.

    Flow: run the attacker macro (Belly Drum turn 1 → Drain Punch until the boss
    dies → Tera from turn 2 → safety cap), which returns once the raid battle is
    over, then either catch the Pokémon (auto_catch / pokemon mode) or just
    collect Herba rewards.
    """
    print(f"{Fore.MAGENTA}[BATTLE]{Style.RESET_ALL} In Battle! Running Iron Hands attacker macro...")

    # 1. Win the raid with real damage (the macro reacts to the on-screen state;
    #    it is authoritative-stopped by the host's pushed FSM state — IN_BATTLE
    #    over the /ws channel — with a vision fallback). Returns once the battle
    #    has concluded.
    result = run_attacker_macro(attacker_cfg, battle_state_fn=host_link.in_battle)
    if result.win:
        print(f"{Fore.GREEN}[BATTLE]{Style.RESET_ALL} Attacker macro finished: "
              f"boss down in {result.turns} turn(s).")
    elif result.reason == "lost":
        print(f"{Fore.RED}[BATTLE]{Style.RESET_ALL} Raid LOST after {result.turns} "
              "turn(s) — the game dropped us back in the overworld with no Catch "
              "button and no Rewards screen.")
    else:
        print(f"{Fore.YELLOW}[BATTLE]{Style.RESET_ALL} Attacker macro finished WITHOUT a "
              f"confirmed win (reason: {result.reason}). Collecting whatever screens follow.")

    # 2. End of raid: catch the Pokémon (always with the configured ball, default
    #    Timer Ball) or just grab rewards.
    #
    #    ONLY on a confirmed win. On a loss there is no Catch button to press: the A
    #    mashing would land on the overworld (talking to scenery / re-entering a den)
    #    and signal_caught would burn the host's den_seed for a Pokémon that got away.
    if auto_catch and result.win:
        print(f"{Fore.GREEN}[CATCH]{Style.RESET_ALL} auto_catch ON — catching with "
              f"{attacker_cfg.catch_ball} (pokemon mode)...")
        catch_pokemon(attacker_cfg)
        # Tell the Host we caught it, so its den_seed is never searched again (clone avoidance).
        if host_api:
            ok = signal_caught(host_api)
            print(f"{Fore.GREEN}[CAUGHT]{Style.RESET_ALL} host notified: {ok}")
    elif auto_catch:
        print(f"{Fore.YELLOW}[CATCH]{Style.RESET_ALL} Skipping the catch — no confirmed "
              f"win ({result.reason}); the host keeps this den_seed for a retry.")
    else:
        print(f"{Fore.GREEN}[REWARDS]{Style.RESET_ALL} Herba mode — collecting rewards (no catch)...")

    # 4. Clear the reward + summary screens by DETECTION (Rewards-label OCR +
    #    text-box/continue-arrow advance) instead of blind mashing — it stops the
    #    instant the overworld minimap is back. (rewards_mash kept for signature compat.)
    print(f"{Fore.GREEN}[REWARDS]{Style.RESET_ALL} Clearing reward and summary screens...")
    clear_rewards(attacker_cfg)

    # 5. Safety net: a few B presses in case a stray prompt lingers past the clear.
    #time.sleep(0.5)
    #click("B", sleep=0.2, repeat=3)
    print(f"{Fore.GREEN}[SUCCESS]{Style.RESET_ALL} Rewards collected! Ready for next cycle.")


# ─────────────────────────────────────────────────────────────────────────────
# TEMP-CALIB — live-frame dumper so the assistant can "watch" while main.py owns
# the capture device (calibrate.py and main.py can't both open the Elgato under
# DSHOW). Mirrors calibrate.py's _frame_dumper. DELETE this whole block (and the
# _start_calib_frame_dumper() call in main()) once the live button-timing pass is
# done. See tera-raid-farmer/HANDOFF-phase2-live.md.
# ─────────────────────────────────────────────────────────────────────────────
_CALIB_DIR = os.path.join(os.path.dirname(__file__), "calib_frames")
_CALIB_LIVE = os.path.join(_CALIB_DIR, "live.png")

# TEMP-CALIB: per-keypress screenshots during link-code entry, so a keypad
# mis-navigation is visible frame-by-frame. Delete with the TEMP-CALIB block.
_KP_SHOT_DIR = os.path.join(_CALIB_DIR, "keypad")
_kp_seq = 0


def _kp_reset() -> None:
    global _kp_seq
    _kp_seq = 0
    try:
        import glob as _g
        os.makedirs(_KP_SHOT_DIR, exist_ok=True)
        for _f in _g.glob(os.path.join(_KP_SHOT_DIR, "*.png")):
            os.remove(_f)
    except Exception:
        pass


def _kp_shot(tag: str) -> None:
    global _kp_seq
    import cv2
    try:
        os.makedirs(_KP_SHOT_DIR, exist_ok=True)
        _kp_seq += 1
        cv2.imwrite(os.path.join(_KP_SHOT_DIR, f"{_kp_seq:03d}_{tag}.png"),
                    capture.get_latest_frame())
    except Exception:
        pass


def _calib_frame_dumper(interval: float = 0.6, dedup_max_diff: float = 8.0) -> None:
    import cv2
    import template_matcher
    os.makedirs(_CALIB_DIR, exist_ok=True)
    last_archived = None
    while not capture.is_stopped():
        try:
            frame = capture.get_latest_frame()
        except Exception:
            time.sleep(interval)
            continue
        tmp = os.path.join(_CALIB_DIR, "live.tmp.png")
        try:
            cv2.imwrite(tmp, frame)
            os.replace(tmp, _CALIB_LIVE)
        except Exception:
            pass
        # Archiving distinct frames floods calib_frames/ with junk; keep only the
        # single overwritten live.png. (Re-enable the archive block if you need a
        # scrollback of distinct screens.)
        time.sleep(interval)


def _start_calib_frame_dumper() -> None:
    threading.Thread(
        target=_calib_frame_dumper, daemon=True, name="CalibFrameDumper"
    ).start()
    print(f"{Fore.LIGHTBLACK_EX}[TEMP-CALIB]{Style.RESET_ALL} live frame -> {_CALIB_LIVE}")
# ── END TEMP-CALIB ───────────────────────────────────────────────────────────


def wake_screen():
    """Wake the Switch 2 screen from its inactivity dim before any vision runs.

    After a while without input the Switch dims the display, which wrecks every
    vision detection (is_in_world, menu option, selected move, etc.). A brief
    right-stick nudge only rotates the overworld camera — an idempotent, harmless
    action — so it is safe to fire before each raid entry to guarantee a bright
    frame. Sign of the Y axis is irrelevant: the right stick never affects state.
    """
    try:
        set_right_stick(0.0, 1.0)
        time.sleep(0.12)
        set_right_stick(0.0, 0.0)
        time.sleep(0.25)  # let the panel finish brightening before we look
    except Exception as exc:  # never let a wake nudge kill the run
        print(f"{Fore.YELLOW}[WAKE]{Style.RESET_ALL} nudge failed: {exc}")


def main():
    _start_calib_frame_dumper()  # TEMP-CALIB — delete with the block above
    config = load_config()
    host_api = config.get("host_api_url", "http://127.0.0.1:8080")
    rewards_mash = config.get("rewards_mash_count", 25)
    auto_catch = config.get("auto_catch", True)
    code_entry_attempts = config.get("code_entry_attempts", 3)
    attacker_cfg = AttackerConfig.from_dict(config)

    # Event-driven link to the host over its /ws push. No HTTP polling: the host
    # pushes the lobby the instant it opens and the FSM state the instant it
    # changes (IN_BATTLE → BATTLE_REWARDS when the boss is down).
    def _hl_log(level, msg):
        color = Fore.YELLOW if level == "WARNING" else Fore.LIGHTBLUE_EX
        print(f"{color}[HOST]{Style.RESET_ALL} {msg}")

    host_link = HostLink(host_api, on_log=_hl_log).start()

    banner = f"""
=============================================================================
  🎮 SWITCH 2 ARDUINO/JOYCON TERA RAID FARMER CLIENT
=============================================================================
  Host push (WebSocket): {host_link.ws_url}
  Controller: Arduino Leonardo/Joy-Con Serial
  Waiting for the host to push a raid lobby...
=============================================================================
"""
    print(Fore.LIGHTCYAN_EX + banner + Style.RESET_ALL)

    farmed_count = 0

    while not capture.is_stopped():
        # Block on the host push (local event wait, not polling). The short
        # timeout only lets us re-check the capture stop flag between waits.
        code = host_link.wait_for_new_lobby(timeout=1.0)
        if not code:
            continue

        print(f"\n{Fore.LIGHTGREEN_EX}🎯 [NEW RAID PUSHED]{Style.RESET_ALL}")
        print(f"  🔑 Code: [{Fore.YELLOW}{code}{Style.RESET_ALL}]")
        raid = host_link.latest_lobby().get("raid") or {}
        if raid:
            print(
                f"  ⭐ Target: {raid.get('stars')}★ {raid.get('species_name')} "
                f"({raid.get('form_name')}) | Shiny: {raid.get('is_shiny')}"
            )
            herba = raid.get("herba_drops", {})
            print(f"  🍃 Herba Drops: {herba.get('total')} total {herba}")

        # 0. Wake the screen — it may have dimmed while we waited for the push.
        #    Must run before any vision (menu nav inside enter_tera_raid_lobby).
        wake_screen()

        # 1. Enter Lobby on Switch 2 via Joycon. Only proceed if we actually joined —
        #    otherwise signalling the host would make it fight the raid solo.
        if not enter_tera_raid_lobby(code, attempts=code_entry_attempts):
            print(f"{Fore.YELLOW}[SKIP]{Style.RESET_ALL} Did not join lobby [{code}]; "
                  "waiting for the host to re-host a fresh lobby.")
            continue

        # 2. Signal Host to start battle immediately (one-shot POST, not polling)
        print(f"{Fore.CYAN}[API]{Style.RESET_ALL} Notifying Host (Switch 1) that Farmer is ready...")
        signal_host_ready(host_api)

        # 3. Run the Iron Hands attacker macro (vision-driven) and collect rewards
        handle_battle_and_rewards(
            attacker_cfg,
            host_link,
            rewards_mash=rewards_mash,
            auto_catch=auto_catch,
            host_api=host_api,
        )
        farmed_count += 1
        print(f"{Fore.LIGHTYELLOW_EX}✨ Total Cycles Farmed on Switch 2: {farmed_count}{Style.RESET_ALL}\n")

    host_link.stop()


def _battle_test():
    """TEMP-CALIB: battle test + MANUAL catch checkpoint (delete with the TEMP block).

    Runs the real join+battle autonomously (Test #1: attacker macro), then — instead
    of auto-catching — hands control to the calib_drive command queue at the
    post-battle screens so the ball selection can be driven manually (Test #2) WITHOUT
    ever throwing a ball. Drop command files in calib_frames/q/*.txt; the throw (final
    A on the ball) is NEVER pressed by this harness — that's the whole point.
    """
    def _fn():
        _start_calib_frame_dumper()
        config = load_config()
        host_api = config.get("host_api_url", "http://127.0.0.1:8080")
        attacker_cfg = AttackerConfig.from_dict(config)

        # Clear any stale queued commands from a previous session so nothing fires
        # on its own the moment we reach the manual checkpoint.
        import glob
        for _stale in glob.glob(os.path.join(_CALIB_DIR, "q", "*.txt")):
            try:
                os.remove(_stale)
            except Exception:
                pass

        def _hl_log(level, msg):
            color = Fore.YELLOW if level == "WARNING" else Fore.LIGHTBLUE_EX
            print(f"{color}[HOST]{Style.RESET_ALL} {msg}")

        host_link = HostLink(host_api, on_log=_hl_log).start()
        print(f"{Fore.LIGHTCYAN_EX}[BATTLE-TEST]{Style.RESET_ALL} waiting for the host to "
              f"push a raid lobby ({host_link.ws_url})...")

        # 1. Block until the host pushes a lobby code.
        code = None
        while not code and not capture.is_stopped():
            code = host_link.wait_for_new_lobby(timeout=1.0)
        if not code:
            print(f"{Fore.YELLOW}[BATTLE-TEST]{Style.RESET_ALL} capture stopped before a "
                  "lobby arrived — exiting.")
            host_link.stop()
            return
        print(f"\n{Fore.LIGHTGREEN_EX}🎯 [LOBBY PUSHED] code=[{code}]{Style.RESET_ALL}")

        # 2. Join the lobby (tested-working) — only fight if we truly joined.
        wake_screen()
        joined = enter_tera_raid_lobby(
            code, attempts=config.get("code_entry_attempts", 3))
        if joined:
            print(f"{Fore.CYAN}[BATTLE-TEST]{Style.RESET_ALL} joined — signalling host to start.")
            signal_host_ready(host_api)
            # 3. Test #1: the real attacker macro (battle under test).
            result = run_attacker_macro(attacker_cfg, battle_state_fn=host_link.in_battle)
            print(f"{Fore.MAGENTA}[BATTLE-TEST]{Style.RESET_ALL} attacker macro done: "
                  f"win={result.win} reason={result.reason} turns={result.turns}")
        else:
            print(f"{Fore.YELLOW}[BATTLE-TEST]{Style.RESET_ALL} join NOT confirmed — NOT "
                  "signalling the host. Dropping to manual so we can inspect the screen.")

        # 4. Test #2: MANUAL catch checkpoint. No auto-catch, no throw. Hand control to
        #    the calib_drive file-queue so the ball selection is driven by hand.
        print(f"{Fore.LIGHTGREEN_EX}[BATTLE-TEST]{Style.RESET_ALL} === MANUAL CATCH CHECKPOINT ===")
        print(f"  Drop commands in {os.path.join(_CALIB_DIR, 'q')}\\*.txt  "
              f"(click/hold/sleep/shot/burst/stop). live.png stays fresh.")
        print(f"  NOTE: this harness NEVER throws the ball on its own — you drive it.")
        import calib_drive
        calib_drive._queue_loop()   # returns on 'stop' command or capture stop
        host_link.stop()

    launch(_fn, debug_regions=_DEBUG_REGIONS)


def _test_enter_lobby(code: str):
    """TEMP-CALIB test entrypoint: run the REAL launch() + enter_tera_raid_lobby()
    for a fixed code (no host needed), then keep the window alive so the entry can
    be observed. Used to validate the lobby-entry flow live. Delete with the
    TEMP-CALIB block."""
    def _fn():
        _start_calib_frame_dumper()
        enter_tera_raid_lobby(
            code, attempts=load_config().get("code_entry_attempts", 3))
        print(f"{Fore.LIGHTBLACK_EX}[TEMP-CALIB]{Style.RESET_ALL} enter done — "
              "Capture window stays open; press q there (or stop the process) to exit.")
        while not capture.is_stopped():
            time.sleep(0.5)
    launch(_fn, debug_regions=_DEBUG_REGIONS)


if __name__ == "__main__":
    # TEMP-CALIB: `python main.py --enter 9015` runs only the real lobby-entry flow.
    if len(sys.argv) >= 3 and sys.argv[1] == "--enter":
        _test_enter_lobby(sys.argv[2])
    # TEMP-CALIB: `python main.py --battle-test` joins the pushed lobby, fights, then
    # stops at a MANUAL catch checkpoint (no auto-catch / no throw) for the ball test.
    elif len(sys.argv) >= 2 and sys.argv[1] == "--battle-test":
        _battle_test()
    else:
        launch(main, debug_regions=_DEBUG_REGIONS)
