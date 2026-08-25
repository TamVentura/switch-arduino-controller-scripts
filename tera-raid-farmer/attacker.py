"""
attacker.py — vision-driven Iron Hands attacker macro for the Switch 2 farmer.

Replaces the old best-effort, sleep-driven Screech support macro. The attacker
reacts to the on-screen battle state (command menu, move list, Tera orb, result
screen) instead of sleeping blind, and uses the host's authoritative FSM state
(pushed over the /ws channel — see host_link.py; IN_BATTLE while the raid runs)
as the primary "is the boss still alive?" signal, falling back to vision when the
host push is unavailable. No HTTP polling.

Combat contract (mirror of the validated host macro):
  1. Turn 1: Belly Drum (belly_drum_slot) — once only (sets +6 Atk).
  2. Turn 2+: Drain Punch (drain_punch_slot) — loop until the boss dies (it heals).
  3. Tera (Fighting) every turn from turn 2 until it takes (idempotent). Never turn 1.
  4. Safety cap (combat_max_turns): on reach while still in battle → WARNING, never
     fake victory.

Design for testability
-----------------------
All decision logic depends ONLY on an injected `BattleIO` (detector/controller/
clock/host seams) and an `AttackerConfig`. This module's top level imports nothing
heavy (no cv2 / pyserial), so it can be imported and unit-tested with fake seams.
`real_io()` wires the real detectors (pokemon_sv), controller (click) and the host
HTTP call lazily, inside the factory.
"""

from __future__ import annotations

import difflib
import time
from dataclasses import dataclass
from typing import Callable, Optional

# ─────────────────────────────────────────────────────────────────────────────
# Config + result + IO seam
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class AttackerConfig:
    """Combat knobs, loaded from tera-raid-farmer/config.json."""

    belly_drum_slot: int = 1
    drain_punch_slot: int = 2
    use_tera: bool = False
    tera_button: str = "R"
    combat_max_turns: int = 50
    turn_poll_timeout_sec: float = 20.0
    move_select_delay_sec: float = 0.6
    host_api_url: str = "http://127.0.0.1:8080"
    # How long to wait for the battle to actually begin before giving up.
    battle_start_timeout_sec: float = 200.0
    # Poll cadence while waiting on a state that isn't a text box.
    vision_poll_interval_sec: float = 0.3
    # ── Vision-gated menu navigation (boss-interrupt resilient) ──
    menu_action_timeout_sec: float = 4.0  # per menu-step bound (open list / commit)
    move_nav_max_steps: int = 6  # cursor steps before giving up a nav attempt
    # ── Catch (pokemon mode) ──
    catch_ball: str = "Timer Ball"  # ball to always use when catching
    catch_timeout_sec: float = 20.0  # max wait for the ball carousel to appear
    # Max right-steps to find the target ball. The carousel is a ring of every ball
    # the player owns (22 live on 2026-08-24), and we only step right, so this must
    # exceed the ring size or a far-side start never reaches the target.
    ball_carousel_max_steps: int = 25
    # Max wait for the post-catch reward/summary screens to clear to the overworld.
    rewards_clear_timeout_sec: float = 90.0

    @classmethod
    def from_dict(cls, cfg: dict) -> "AttackerConfig":
        return cls(
            belly_drum_slot=int(cfg.get("belly_drum_slot", 1)),
            drain_punch_slot=int(cfg.get("drain_punch_slot", 2)),
            use_tera=bool(cfg.get("use_tera", True)),
            tera_button=str(cfg.get("tera_button", "R")),
            combat_max_turns=int(cfg.get("combat_max_turns", 50)),
            turn_poll_timeout_sec=float(cfg.get("turn_poll_timeout_sec", 20.0)),
            move_select_delay_sec=float(cfg.get("move_select_delay_sec", 0.6)),
            host_api_url=str(cfg.get("host_api_url", "http://127.0.0.1:8080")),
            battle_start_timeout_sec=float(cfg.get("battle_start_timeout_sec", 200.0)),
            vision_poll_interval_sec=float(cfg.get("vision_poll_interval_sec", 0.3)),
            menu_action_timeout_sec=float(cfg.get("menu_action_timeout_sec", 4.0)),
            move_nav_max_steps=int(cfg.get("move_nav_max_steps", 6)),
            catch_ball=str(cfg.get("catch_ball", "Timer Ball")),
            catch_timeout_sec=float(cfg.get("catch_timeout_sec", 20.0)),
            ball_carousel_max_steps=int(cfg.get("ball_carousel_max_steps", 25)),
            rewards_clear_timeout_sec=float(cfg.get("rewards_clear_timeout_sec", 90.0)),
        )


@dataclass
class AttackerResult:
    """Outcome of run_attacker_macro."""

    win: bool
    reason: str  # "boss-defeated" | "lost" | "battle-never-started" |
    # "max-turns" (unconfirmed)
    turns: int = 0

    @property
    def confirmed(self) -> bool:
        """True only when the outcome was positively detected (not a timeout)."""
        return self.reason in ("boss-defeated", "lost")


@dataclass
class BattleIO:
    """Injectable seams. Defaults are placeholders; use real_io() for hardware."""

    click: Callable[..., None]
    is_command_menu: Callable[[], bool]
    is_move_select: Callable[[], bool]
    tera_available: Callable[[], bool]
    battle_ended: Callable[[], bool]
    battle_lost: Callable[[], bool]
    at_text_box: Callable[[], bool]
    # Host combat flag: True = in battle, False = not in battle, None = unknown.
    battle_state: Callable[[], Optional[bool]]
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], float] = time.monotonic
    # Which move row (1-4) is highlighted, 0 if none / not in the move list.
    get_move_slot: Callable[[], int] = lambda: 0
    # Catch seams (OCR-driven ball carousel). Defaults keep tests/other flows working.
    read_ball_name: Callable[[], str] = lambda: ""
    at_ball_select: Callable[[], bool] = lambda: False
    # True on the post-victory Catch / Don't-Catch prompt (= boss defeated).
    at_catch_prompt: Callable[[], bool] = lambda: False
    # Short D-pad tap (single cell). A normal click holds ~0.2s → the carousel/keypad
    # auto-repeats and skips a cell; a 50ms tap moves exactly one (validated live).
    tap: Callable[..., None] = lambda *a, **k: None
    # True on the raid result "Rewards" screen (OCR of the 'Rewards' label).
    at_rewards: Callable[[], bool] = lambda: False
    # True once back in the overworld (minimap in the corner).
    in_overworld: Callable[[], bool] = lambda: False


# ─────────────────────────────────────────────────────────────────────────────
# Real IO factory (heavy imports live here, not at module top)
# ─────────────────────────────────────────────────────────────────────────────


def real_io(battle_state_fn: Optional[Callable[[], Optional[bool]]] = None) -> BattleIO:
    """Wire the real detectors / controller. Imported lazily so unit tests can
    import this module without cv2 / pyserial present.

    battle_state_fn: the host's in-battle signal (True/False/None) — normally
    HostLink.in_battle from the /ws push. When None, the macro relies on vision
    alone for the stop condition (battle_ended()).
    """
    import pokemon_sv
    import capture
    from controller import click as _click
    from controller import hold_for as _hold_for

    def _at_text_box() -> bool:
        try:
            return pokemon_sv.get_mode() in (pokemon_sv.Mode.TEXT_BOX_CONTINUE,)
        except Exception:
            return False

    def _read_ball_name() -> str:
        # OCR the centered ball-name banner on the catch carousel (4K coords,
        # calibrated 2026-08-18: OCR reads "TimerBall" here).
        try:
            import capture

            return pokemon_sv.ocr.read_field(
                capture.get_latest_frame(), 1720, 1476, 2340, 1590
            )
        except Exception:
            return ""

    def _at_ball_select() -> bool:
        # Heuristic: the ball carousel is open when the banner OCRs to a *Ball name.
        return "ball" in _read_ball_name().lower()

    def _at_catch_prompt() -> bool:
        # Gold "Catch" button (#ffe300) on the post-victory Catch/Don't-Catch prompt.
        # Calibrated live 2026-08-24: this region reads ~0.9 on the catch prompt and
        # ~0 elsewhere. It partially overlaps the command menu's gold "Battle" button,
        # so boss_defeated() gates this with `not is_command_menu()`.
        try:
            return (
                capture.region_color_ratio(
                    3100, 1780, 3740, 1880, "#ffe300", tolerance=80
                )
                >= 0.5
            )
        except Exception:
            return False

    def _tap(token, sleep=0.0):
        # 50ms D-pad tap → one cell (no auto-repeat double-step). See BattleIO.tap.
        _hold_for(token, 0.05, sleep=sleep)

    def _at_rewards() -> bool:
        # OCR the raid-result "Rewards" label (top-left of the result screen).
        # Calibrated live 2026-08-24: reads "Rewards" at this 4K region.
        try:
            return (
                "reward"
                in pokemon_sv.ocr.read_field(
                    capture.get_latest_frame(), 296, 464, 556, 544
                ).lower()
            )
        except Exception:
            return False

    def _in_overworld() -> bool:
        try:
            return pokemon_sv.get_mode() == pokemon_sv.Mode.IN_GAME
        except Exception:
            return False

    return BattleIO(
        click=_click,
        is_command_menu=pokemon_sv.is_command_menu,
        is_move_select=pokemon_sv.is_move_select,
        tera_available=pokemon_sv.tera_available,
        battle_ended=pokemon_sv.battle_ended,
        battle_lost=pokemon_sv.battle_lost,
        at_text_box=_at_text_box,
        battle_state=battle_state_fn or (lambda: None),
        get_move_slot=pokemon_sv.get_move_slot,
        read_ball_name=_read_ball_name,
        at_ball_select=_at_ball_select,
        at_catch_prompt=_at_catch_prompt,
        tap=_tap,
        at_rewards=_at_rewards,
        in_overworld=_in_overworld,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Primitives
# ─────────────────────────────────────────────────────────────────────────────


def boss_defeated(io: BattleIO) -> bool:
    """True when the boss is down. **Vision is authoritative — never the host flag.**

    The host only exposes an ambiguous scene byte that reads "in battle" all the
    way through victory, rewards and the catch screen, so it cannot distinguish
    boss-alive from boss-dead; on a loss it would also report a false win. We
    therefore decide purely from what is on screen (battle_ended()).

    Only meaningful AFTER the battle has started (see wait_for_battle_start),
    because battle_ended()'s minimap check is also true in the pre-battle lobby.

    The instant the boss dies the game shows the Catch / Don't-Catch prompt, which is
    NOT the overworld and NOT the rewards screen — so battle_ended() alone stays False
    there and the old macro spun on turn-timeouts until the raid finally ended. We also
    treat that catch prompt as "boss down", gated by `not is_command_menu()` so the
    command menu's gold 'Battle' button (which overlaps the 'Catch' button region) can
    never be misread as a defeat mid-fight.
    """
    return io.battle_ended() or (io.at_catch_prompt() and not io.is_command_menu())


def wait_for_battle_start(cfg: AttackerConfig, io: BattleIO) -> bool:
    """Poll until the raid battle actually begins ON THIS SCREEN — the command or
    move menu appears (vision). The host flag is deliberately NOT trusted: the host
    starts its side the instant we POST start_battle whether or not we truly joined
    the lobby, so a host IN_BATTLE flag does not mean Switch 2 is in the fight.
    Requiring the actual battle UI makes a failed join time out fast (returns False)
    instead of flailing through empty turns. Advances any pre-battle dialogue."""
    deadline = io.now() + cfg.battle_start_timeout_sec
    while io.now() < deadline:
        if io.is_command_menu() or io.is_move_select():
            return True
        if io.at_text_box():
            io.click("A", sleep=0.1)
        else:
            io.sleep(cfg.vision_poll_interval_sec)
    return False


def wait_for_my_turn(cfg: AttackerConfig, io: BattleIO) -> str:
    """Poll for the next actionable state.

    Returns:
      "turn"    — the command menu is open (act now)
      "ended"   — the boss is defeated (stop attacking)
      "timeout" — neither happened within turn_poll_timeout_sec

    Only presses A on a detected text box, never blindly, so it can never
    mis-select a move.
    """
    deadline = io.now() + cfg.turn_poll_timeout_sec
    while io.now() < deadline:
        if boss_defeated(io):
            return "ended"
        if io.is_command_menu():
            return "turn"
        if io.at_text_box():
            io.click("A", sleep=0.1)
        else:
            io.sleep(cfg.vision_poll_interval_sec)
    return "timeout"


def try_tera(cfg: AttackerConfig, io: BattleIO) -> None:
    """Press the Terastal button (R). In SV raids this is done from the MOVE-SELECT
    screen where the 'TERASTALLIZE! [R]' prompt appears (confirmed live 2026-08-18).
    Idempotent — re-pressing after Tera is already active is a no-op, so it's safe
    to attempt every turn from turn 2."""
    io.click(cfg.tera_button, sleep=cfg.move_select_delay_sec)


def open_move_list(cfg: AttackerConfig, io: BattleIO) -> bool:
    """Ensure the move list is open, tolerating boss interrupts. Presses A on the
    command menu AT MOST once per poll (opening 'Battle'), advances text boxes, and
    only returns True once vision confirms `is_move_select()`. Bounded by
    menu_action_timeout_sec. Never walks into a committed move."""
    deadline = io.now() + cfg.menu_action_timeout_sec
    while io.now() < deadline:
        if io.is_move_select():
            return True
        if io.is_command_menu():
            io.click("A", sleep=cfg.move_select_delay_sec)  # Battle → open move list
        elif io.at_text_box():
            io.click("A", sleep=0.1)  # advance pre-turn dialogue
        else:
            io.sleep(cfg.vision_poll_interval_sec)  # boss animating; wait
    return io.is_move_select()


def goto_move_slot(slot: int, cfg: AttackerConfig, io: BattleIO) -> bool:
    """Move the move-list cursor onto the 1-indexed `slot`, vision-gated: read the
    highlighted slot and step by the exact delta (Down '2' / Up '4'), re-reading each
    step. Delta-stepping is correct whether the list clamps or wraps at the ends
    (same as goto_portal_option). Returns False if the list is dismissed mid-nav
    (get_move_slot() == 0 → boss interrupt) so the caller can re-open. Bounded by
    move_nav_max_steps."""
    for _ in range(cfg.move_nav_max_steps):
        cur = io.get_move_slot()
        if cur == 0:
            return False  # list gone (boss acted) — caller re-opens
        if cur == slot:
            return True
        move = slot - cur
        io.click(
            "2" if move > 0 else "4", sleep=cfg.move_select_delay_sec, repeat=abs(move)
        )
    return io.get_move_slot() == slot


def commit_move(cfg: AttackerConfig, io: BattleIO) -> bool:
    """Commit the highlighted move: press A (raid boss is auto-targeted, single
    target), then confirm via vision that the action locked in.

    A genuine commit leaves the move list for the post-action waiting/resolution
    screen (neither move list nor command menu). If instead we bounce back to the
    command menu, the press did NOT commit (a boss interrupt returned our turn) →
    return False so the caller re-opens and retries. This distinction is what stops
    a mis-fire from being mistaken for a committed move."""
    io.click("A", sleep=1)
    io.click("A", sleep=cfg.move_select_delay_sec)
    deadline = io.now() + cfg.menu_action_timeout_sec
    while io.now() < deadline:
        if io.is_command_menu():
            return False  # bounced to command menu — didn't commit
        if not io.is_move_select():
            return True  # into resolution/waiting — committed
        io.sleep(cfg.vision_poll_interval_sec)
    return not io.is_move_select() and not io.is_command_menu()


def take_turn(slot: int, want_tera: bool, cfg: AttackerConfig, io: BattleIO) -> bool:
    """Play one turn onto the 1-indexed move `slot`, resilient to the real-time raid
    boss dismissing the menu mid-navigation.

    Every button press is vision-gated (open_move_list → optional Tera →
    goto_move_slot → commit_move); a boss interrupt at any point drops control back to
    re-assessment rather than letting a blind press land on the wrong screen. This is
    what prevents stray A's from re-opening Battle and re-casting the top slot (the
    old blind-sequence 'always Belly Drum' bug).

    Returns True once an action is locked in, False if it couldn't act this pass (the
    outer macro loop simply retries). Confirmed facts (live 2026-08-19): command-menu
    'Battle' opens the move list with one A; the boss is auto-targeted so one A
    commits; Tera = R from the move list, idempotent."""
    deadline = io.now() + cfg.menu_action_timeout_sec * 4  # whole-turn budget
    while io.now() < deadline:
        if boss_defeated(io):
            return True  # nothing left to do
        if not open_move_list(cfg, io):
            continue  # boss acting / no menu yet — reassess
        if want_tera and cfg.use_tera and io.tera_available():
            try_tera(cfg, io)  # R → Terastallize (stays in list; best-effort)
        if not goto_move_slot(slot, cfg, io):
            continue  # list dismissed mid-nav — reassess
        if commit_move(cfg, io):
            return True  # action locked in
        # commit didn't take (boss interrupt) — loop and reassess
    return False


def _norm(s: str) -> str:
    return "".join(s.lower().split())


def catch_pokemon(cfg: AttackerConfig, io: Optional[BattleIO] = None) -> bool:
    """Catch the raid boss with the configured ball (default Timer Ball).

    Called after the boss is defeated, on the post-battle Catch prompt. Flow
    (observed live 2026-08-18): advance text and choose 'Catch' (the default,
    so A) until the ball carousel appears, then OCR the centered ball name and
    press Right until it reads the target ball, then A to throw.

    Returns True if it reached the throw on the target ball, False if it fell back
    to whatever ball was selected. NEEDS LIVE BUTTON-TIMING CONFIRMATION (the A
    that opens the carousel must not overshoot into an early throw; the carousel
    wraps, so navigation is bounded by ball_carousel_max_steps).
    """
    if io is None:
        io = real_io()
    log = _logger()
    d = cfg.move_select_delay_sec

    # 1. Advance post-battle text and accept 'Catch' (default) until the carousel
    #    appears. Stop the instant it's detected so A doesn't throw the wrong ball.
    deadline = io.now() + cfg.catch_timeout_sec
    while io.now() < deadline and not io.at_ball_select():
        io.click("A", sleep=0.15)
        io.sleep(cfg.vision_poll_interval_sec)

    if not io.at_ball_select():
        log("WARNING", "ball carousel not detected — throwing the default ball")
        io.click("A", sleep=d)
        return False

    # 2. OCR-navigate the carousel to the target ball (wraps, so bounded).
    target = _norm(cfg.catch_ball)
    for _ in range(cfg.ball_carousel_max_steps):
        name = _norm(io.read_ball_name())
        if target and (
            target in name or difflib.SequenceMatcher(None, name, target).ratio() > 0.7
        ):
            log("CATCH", f"ball selected: {cfg.catch_ball}")
            io.click("A", sleep=d)  # throw
            return True
        io.tap("1", sleep=d)  # D-Pad Right → next ball (short tap: one ball,
        #                                 a 0.2s click auto-repeats and skips one)

    log(
        "WARNING",
        f"'{cfg.catch_ball}' not found in {cfg.ball_carousel_max_steps} "
        "steps — throwing the current ball",
    )
    io.click("A", sleep=d)
    return False


def clear_rewards(cfg: AttackerConfig, io: Optional[BattleIO] = None) -> bool:
    """Detection-driven clearing of the post-catch screens — replaces blind A/B
    mashing.

    After the throw the game shows: the catch/"You caught X!" result with its
    'Rewards' list ('A' Next), then a Pokédex "sent to your Boxes!" message box (the
    dark-box continue arrow), then the overworld. We advance A ONLY on a detected
    message (at_text_box) or Rewards page (at_rewards, OCR of the 'Rewards' label),
    wait out animations / "Communicating…", and stop the instant the minimap is back
    (in_overworld). Bounded by rewards_clear_timeout_sec. Returns True if it reached
    the overworld."""
    if io is None:
        io = real_io()
    log = _logger()
    deadline = io.now() + cfg.rewards_clear_timeout_sec
    while io.now() < deadline:
        if io.at_text_box():
            time.sleep(1)  # wait for the continue arrow to appear (animation)
            io.click("A", sleep=0.4)  # advance a message / continue arrow
            return True
        elif io.at_rewards():
            io.click("A", sleep=0.5)  # 'Next' through the Rewards result
        else:
            io.sleep(cfg.vision_poll_interval_sec)  # catch animation / "Communicating…"
    log("WARNING", "post-battle clear timed out before reaching the overworld")
    return False


# ─────────────────────────────────────────────────────────────────────────────
# The macro
# ─────────────────────────────────────────────────────────────────────────────


def run_attacker_macro(
    cfg: AttackerConfig,
    io: Optional[BattleIO] = None,
    battle_state_fn: Optional[Callable[[], Optional[bool]]] = None,
) -> AttackerResult:
    """Run the full Iron Hands attacker macro for one raid. See module docstring
    for the combat contract.

    battle_state_fn: the host's in-battle signal (HostLink.in_battle from the /ws
    push). Ignored when an explicit `io` is supplied (tests inject their own).
    """
    if io is None:
        io = real_io(battle_state_fn)

    log = _logger()

    # 1. Wait for the battle to actually begin before arming end-detection.
    if not wait_for_battle_start(cfg, io):
        log("WARNING", "battle never started (no command menu / host flag) — aborting")
        return AttackerResult(win=False, reason="battle-never-started", turns=0)

    # 2. Turn 1 — Belly Drum, once only.
    r = wait_for_my_turn(cfg, io)
    if r == "ended":
        log("OK", "boss already down before turn 1")
        return AttackerResult(win=True, reason="boss-defeated", turns=0)
    if io.battle_lost():
        return AttackerResult(win=False, reason="lost", turns=0)
    if r == "turn":
        log("BATTLE", f"Turn 1: Belly Drum (slot {cfg.belly_drum_slot})")
        take_turn(
            cfg.belly_drum_slot, want_tera=False, cfg=cfg, io=io
        )  # never Tera turn 1
    else:  # timeout on turn 1 — re-check, then fall through to the Drain Punch loop
        log("WARNING", "turn 1 timed out waiting for the command menu")
        if boss_defeated(io):
            return AttackerResult(win=True, reason="boss-defeated", turns=0)

    # 3. Turn 2+ — Drain Punch until the boss dies.
    turn = 2
    while turn <= cfg.combat_max_turns:
        if boss_defeated(io):
            log("OK", f"boss defeated after {turn - 1} attacking turn(s)")
            return AttackerResult(win=True, reason="boss-defeated", turns=turn - 1)
        if io.battle_lost():
            log("WARNING", "battle lost (KO / timeout)")
            return AttackerResult(win=False, reason="lost", turns=turn - 1)

        r = wait_for_my_turn(cfg, io)
        if r == "ended":
            log("OK", f"boss defeated after {turn - 1} attacking turn(s)")
            return AttackerResult(win=True, reason="boss-defeated", turns=turn - 1)
        if r == "timeout":
            if boss_defeated(io):
                log("OK", "boss defeated (confirmed after turn timeout)")
                return AttackerResult(win=True, reason="boss-defeated", turns=turn - 1)
            log("WARNING", f"turn {turn} timed out — boss not confirmed dead, retrying")
            turn += 1
            continue

        # It's my turn: Drain Punch, Terastallizing (from turn 2) if the prompt is
        # up. take_turn checks tera_available inside the move list and presses R.
        log(
            "BATTLE",
            f"Turn {turn}: Drain Punch (slot {cfg.drain_punch_slot})"
            + (" + Tera" if cfg.use_tera else ""),
        )
        take_turn(cfg.drain_punch_slot, want_tera=True, cfg=cfg, io=io)
        turn += 1

    # 4. Safety cap reached while still in battle.
    log(
        "WARNING",
        f"safety cap ({cfg.combat_max_turns} turns) reached — boss NOT "
        "confirmed dead; not faking victory",
    )
    return AttackerResult(win=False, reason="max-turns", turns=cfg.combat_max_turns)


# ─────────────────────────────────────────────────────────────────────────────
# Logging (colour when available, plain otherwise)
# ─────────────────────────────────────────────────────────────────────────────


def _logger():
    try:
        from colorama import Fore, Style

        colors = {
            "OK": Fore.GREEN,
            "BATTLE": Fore.MAGENTA,
            "WARNING": Fore.YELLOW,
            "INFO": Fore.CYAN,
        }

        def log(level: str, msg: str) -> None:
            c = colors.get(level, "")
            print(f"{c}[{level}]{Style.RESET_ALL} {msg}")

    except Exception:

        def log(level: str, msg: str) -> None:
            print(f"[{level}] {msg}")

    return log
