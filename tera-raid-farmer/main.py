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
    MenuOption,
    goto_menu_option,
    PortalOption,
    goto_portal_option,
)
from colorama import Fore, Style

# Vision-driven Iron Hands attacker macro (Switch 2 is now the ATTACKER).
from attacker import AttackerConfig, run_attacker_macro, catch_pokemon
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
        "auto_catch": True,
        "catch_ball": "Timer Ball",  # pokemon mode: always catch with this ball
        # ── Iron Hands attacker macro (Switch 2 is now the ATTACKER) ──
        "belly_drum_slot": 1,       # move-slot of Belly Drum (turn 1, once)
        "drain_punch_slot": 2,      # move-slot of Drain Punch (turn 2+, loop)
        "use_tera": True,           # Terastallize (Fighting) from turn 2
        "tera_button": "R",         # controller token for Terastal
        "combat_max_turns": 50,     # safety cap against an infinite loop
        "turn_poll_timeout_sec": 20,   # max wait for "it's my turn" per turn
        "battle_start_timeout_sec": 45,  # max wait for the battle to begin
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


def type_link_code_numeric(code: str, step: float = 0.12) -> None:
    """Type a numeric link code on the Switch 2 Tera-Raid keypad (3×4 grid) and
    confirm with '+' (OK), which also fires the search immediately.

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
    cur_r, cur_c = 0, 0  # keypad always opens on '1'

    for ch in str(code):
        if ch not in _KEYPAD_POS:
            continue
        tr, tc = _KEYPAD_POS[ch]

        # Leaving the lone '0' cell first: step Up into the 3×3 grid (0 → 8).
        if (cur_r, cur_c) == (3, 1) and (tr, tc) != (3, 1):
            click("4", sleep=step)          # Up
            cur_r, cur_c = 2, 1

        if (tr, tc) == (3, 1):
            # Target '0': align to column 1 (in a digit row), then Down to the
            # bottom (Down clamps on '0').
            if (cur_r, cur_c) != (3, 1):
                dc = 1 - cur_c
                if dc:
                    click("1" if dc > 0 else "3", sleep=step, repeat=abs(dc))
                click("2", sleep=step, repeat=3)   # Down → '0'
                cur_r, cur_c = 3, 1
        else:
            # Column first (stays in rows 0-2), then row — never enters row 3, and
            # each step moves toward the target so we never press past an edge.
            dc = tc - cur_c
            if dc:
                click("1" if dc > 0 else "3", sleep=step, repeat=abs(dc))
            dr = tr - cur_r
            if dr:
                click("2" if dr > 0 else "4", sleep=step, repeat=abs(dr))
            cur_r, cur_c = tr, tc

        click("A", sleep=step)              # enter the digit; cursor stays

    time.sleep(0.2)
    click("+", sleep=2.0)                   # OK — confirms the code AND searches


def enter_tera_raid_lobby(code: str) -> None:
    """Join the host's Tera Raid lobby on Switch 2: Main Menu → Poké Portal → Tera
    Raid Battle → Link Code keypad → type `code` → search → confirm 'Ready!'.

    Both menu hops are vision-driven (detect the current highlight, move by the
    delta): goto_menu_option for the main menu, goto_portal_option for the Poké
    Portal submenu — so it works regardless of where each cursor last sat."""
    print(f"{Fore.CYAN}[FARMER]{Style.RESET_ALL} Main Menu -> Poke Portal -> Tera Raid Battle...")

    # 1. Main Menu → Poké Portal (dynamic).
    try:
        print(f"{Fore.LIGHTBLUE_EX}[VISION]{Style.RESET_ALL} Current Game Mode: {get_mode()}")
        goto_menu_option(MenuOption.PORTAL)
    except Exception as e:
        print(f"{Fore.YELLOW}[WARN]{Style.RESET_ALL} goto_menu_option fallback: {e}")
        click("B", sleep=0.5, repeat=2)
        click("X", sleep=1.5)
    click("A", sleep=3.5)                    # open the Poké Portal

    # 2. Poké Portal → Tera Raid Battle (dynamic).
    if not goto_portal_option(PortalOption.TERA_RAID):
        print(f"{Fore.YELLOW}[WARN]{Style.RESET_ALL} portal option not confirmed; "
              "assuming cursor is on Tera Raid Battle")
    click("A", sleep=4.0)                    # → Tera Raid Battle Search (Link Code card)

    # 3. Open the Link Code keypad ('Link Code' card is pre-selected).
    click("A", sleep=1.5)

    # 4. Type the code and confirm — '+' also starts the search.
    type_link_code_numeric(code)

    # 5. Enter the lobby and confirm 'Ready!'. The search is already running; a real
    #    host raid transitions into the lobby, where A advances into it and confirms
    #    readiness. (The attacker macro then detects battle-start via vision.)
    print(f"{Fore.CYAN}[FARMER]{Style.RESET_ALL} Confirming 'Ready!' in raid lobby...")
    click("A", sleep=3.0)
    click("A", sleep=1.5)


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
    else:
        print(f"{Fore.YELLOW}[BATTLE]{Style.RESET_ALL} Attacker macro finished WITHOUT a "
              f"confirmed win (reason: {result.reason}). Collecting whatever screens follow.")

    # 2. End of raid: catch the Pokémon (always with the configured ball, default
    #    Timer Ball) or just grab rewards.
    if auto_catch:
        print(f"{Fore.GREEN}[CATCH]{Style.RESET_ALL} auto_catch ON — catching with "
              f"{attacker_cfg.catch_ball} (pokemon mode)...")
        catch_pokemon(attacker_cfg)
        # Tell the Host we caught it, so its den_seed is never searched again (clone avoidance).
        if host_api:
            ok = signal_caught(host_api)
            print(f"{Fore.GREEN}[CAUGHT]{Style.RESET_ALL} host notified: {ok}")
    else:
        print(f"{Fore.GREEN}[REWARDS]{Style.RESET_ALL} Herba mode — collecting rewards (no catch)...")

    # 4. Reuse the existing reward-mash / drop-loot-screens logic.
    print(f"{Fore.GREEN}[REWARDS]{Style.RESET_ALL} Clearing reward and summary screens...")
    for _ in range(rewards_mash):
        click("A", sleep=0.1)
        click("B", sleep=0.1)

    # 5. Return cleanly to overworld.
    time.sleep(1.0)
    click("B", sleep=0.2, repeat=3)
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
        try:
            is_new = last_archived is None or not template_matcher.fast_equals(
                frame, last_archived, max_diff=dedup_max_diff)
        except Exception:
            is_new = False
        if is_new:
            fn = os.path.join(
                _CALIB_DIR,
                time.strftime("%H%M%S") + f"_{int(time.time() * 1000) % 1000:03d}.png",
            )
            try:
                cv2.imwrite(fn, frame)
                last_archived = frame.copy()
            except Exception:
                pass
        time.sleep(interval)


def _start_calib_frame_dumper() -> None:
    threading.Thread(
        target=_calib_frame_dumper, daemon=True, name="CalibFrameDumper"
    ).start()
    print(f"{Fore.LIGHTBLACK_EX}[TEMP-CALIB]{Style.RESET_ALL} live frame -> {_CALIB_LIVE}")
# ── END TEMP-CALIB ───────────────────────────────────────────────────────────


def main():
    _start_calib_frame_dumper()  # TEMP-CALIB — delete with the block above
    config = load_config()
    host_api = config.get("host_api_url", "http://127.0.0.1:8080")
    rewards_mash = config.get("rewards_mash_count", 25)
    auto_catch = config.get("auto_catch", True)
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

        # 1. Enter Lobby on Switch 2 via Joycon
        enter_tera_raid_lobby(code)

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


def _test_enter_lobby(code: str):
    """TEMP-CALIB test entrypoint: run the REAL launch() + enter_tera_raid_lobby()
    for a fixed code (no host needed), then keep the window alive so the entry can
    be observed. Used to validate the lobby-entry flow live. Delete with the
    TEMP-CALIB block."""
    def _fn():
        _start_calib_frame_dumper()
        enter_tera_raid_lobby(code)
        print(f"{Fore.LIGHTBLACK_EX}[TEMP-CALIB]{Style.RESET_ALL} enter done — "
              "Capture window stays open; press q there (or stop the process) to exit.")
        while not capture.is_stopped():
            time.sleep(0.5)
    launch(_fn, debug_regions=_DEBUG_REGIONS)


if __name__ == "__main__":
    # TEMP-CALIB: `python main.py --enter 9015` runs only the real lobby-entry flow.
    if len(sys.argv) >= 3 and sys.argv[1] == "--enter":
        _test_enter_lobby(sys.argv[2])
    else:
        launch(main, debug_regions=_DEBUG_REGIONS)
