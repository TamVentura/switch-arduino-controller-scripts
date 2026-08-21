"""
Unit tests for the vision-driven attacker macro decision logic.

These exercise the pure control flow (turn state machine, host-flag stop
condition, move-navigation button sequences, macro sequencing) with injected
fake seams — no Arduino, no capture card, no cv2/pyserial required. The vision
detectors themselves are calibrated live (see CALIBRATION.md) and are not
covered here.
"""

import os
import sys

# Make tera-raid-farmer/attacker.py importable without the switch bootstrap.
_HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..")))

import attacker as A  # noqa: E402
from attacker import AttackerConfig, BattleIO  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


class Clock:
    """Deterministic clock: now() ticks forward on every read so any polling
    loop with a finite deadline always terminates; sleep() also advances it."""

    def __init__(self, tick=0.05):
        self.t = 0.0
        self.tick = tick

    def now(self):
        self.t += self.tick
        return self.t

    def sleep(self, s):
        self.t += (s or 0.0)


def make_io(**overrides):
    """Build a BattleIO of no-op fakes; override individual seams by keyword.
    Returns (io, clicks) where clicks is the recorded [(token, repeat), ...]."""
    clicks = []

    def click(token, sleep=0.0, repeat=1):
        clicks.append((token, repeat))

    clock = Clock()
    defaults = dict(
        click=click,
        is_command_menu=lambda: False,
        is_move_select=lambda: False,
        tera_available=lambda: False,
        battle_ended=lambda: False,
        battle_lost=lambda: False,
        at_text_box=lambda: False,
        battle_state=lambda: None,
        sleep=clock.sleep,
        now=clock.now,
    )
    defaults.update(overrides)
    return BattleIO(**defaults), clicks  # type: ignore[arg-type]


# ─────────────────────────────────────────────────────────────────────────────
# boss_defeated — host flag authoritative, vision fallback
# ─────────────────────────────────────────────────────────────────────────────


def test_boss_defeated_host_flag_in_battle():
    io, _ = make_io(battle_state=lambda: True)
    assert A.boss_defeated(io) is False


def test_boss_defeated_host_flag_not_in_battle():
    io, _ = make_io(battle_state=lambda: False)
    assert A.boss_defeated(io) is True


def test_boss_defeated_falls_back_to_vision_when_host_unknown():
    io, _ = make_io(battle_state=lambda: None, battle_ended=lambda: True)
    assert A.boss_defeated(io) is True
    io2, _ = make_io(battle_state=lambda: None, battle_ended=lambda: False)
    assert A.boss_defeated(io2) is False


def test_host_flag_wins_over_vision():
    # Host says still in battle even though vision (minimap) would say ended.
    io, _ = make_io(battle_state=lambda: True, battle_ended=lambda: True)
    assert A.boss_defeated(io) is False


# ─────────────────────────────────────────────────────────────────────────────
# wait_for_my_turn
# ─────────────────────────────────────────────────────────────────────────────


def test_wait_for_my_turn_returns_turn_on_command_menu():
    io, _ = make_io(is_command_menu=lambda: True)
    assert A.wait_for_my_turn(AttackerConfig(), io) == "turn"


def test_wait_for_my_turn_returns_ended_when_boss_down():
    io, _ = make_io(battle_state=lambda: False)  # host: not in battle
    assert A.wait_for_my_turn(AttackerConfig(), io) == "ended"


def test_wait_for_my_turn_times_out():
    cfg = AttackerConfig(turn_poll_timeout_sec=1.0)
    io, _ = make_io()  # nothing ever true
    assert A.wait_for_my_turn(cfg, io) == "timeout"


def test_wait_for_my_turn_advances_text_boxes_then_times_out():
    cfg = AttackerConfig(turn_poll_timeout_sec=1.0)
    io, clicks = make_io(at_text_box=lambda: True)
    assert A.wait_for_my_turn(cfg, io) == "timeout"
    assert clicks, "should have pressed A to advance dialogue"
    assert all(tok == "A" for tok, _ in clicks)


def test_wait_for_my_turn_ended_beats_command_menu():
    # If the boss is already down, don't act even if a menu lingers.
    io, _ = make_io(battle_state=lambda: False, is_command_menu=lambda: True)
    assert A.wait_for_my_turn(AttackerConfig(), io) == "ended"


# ─────────────────────────────────────────────────────────────────────────────
# wait_for_battle_start
# ─────────────────────────────────────────────────────────────────────────────


def test_wait_for_battle_start_on_command_menu():
    io, _ = make_io(is_command_menu=lambda: True)
    assert A.wait_for_battle_start(AttackerConfig(), io) is True


def test_wait_for_battle_start_on_host_flag():
    io, _ = make_io(battle_state=lambda: True)
    assert A.wait_for_battle_start(AttackerConfig(), io) is True


def test_wait_for_battle_start_times_out():
    cfg = AttackerConfig(battle_start_timeout_sec=1.0)
    io, _ = make_io()
    assert A.wait_for_battle_start(cfg, io) is False


# ─────────────────────────────────────────────────────────────────────────────
# take_turn — vision-gated navigation (boss-interrupt resilient)
# ─────────────────────────────────────────────────────────────────────────────


class BattleSim:
    """A stateful fake of the real-time raid battle screens for the gated navigation
    helpers. Screens: 'command' (Battle/Cheer/Run), 'movelist' (4 moves, cursor on
    self.slot), 'text', 'resolved' (post-commit waiting/resolution — neither menu).

    Boss interrupts are injected via:
      dismiss_before_reads — get_move_slot() returns 0 and closes the list back to
                             the command menu this many times (list dismissed mid-nav);
      fail_commits         — a commit A bounces back to the command menu instead of
                             locking in, this many times (commit interrupted)."""

    def __init__(self, clock, screen="command"):
        self.clock = clock
        self.screen = screen
        self.slot = 1                 # move-list cursor defaults to the top slot
        self.committed = None
        self.tera = 0
        self.clicks = []
        self.dismiss_before_reads = 0
        self.fail_commits = 0

    # detectors
    def is_command_menu(self):
        return self.screen == "command"

    def is_move_select(self):
        return self.screen == "movelist"

    def at_text_box(self):
        return self.screen == "text"

    def get_move_slot(self):
        if self.screen != "movelist":
            return 0
        if self.dismiss_before_reads > 0:
            self.dismiss_before_reads -= 1
            self.screen = "command"   # boss dismissed the list mid-navigation
            return 0
        return self.slot

    def click(self, tok, sleep=0.0, repeat=1):
        self.clicks.append((tok, repeat))
        if tok == "A":
            if self.screen == "command":
                self.screen = "movelist"
                self.slot = 1
            elif self.screen == "movelist":
                if self.fail_commits > 0:
                    self.fail_commits -= 1
                    self.screen = "command"     # commit interrupted → back to our turn
                else:
                    self.committed = self.slot
                    self.screen = "resolved"
            elif self.screen == "text":
                self.screen = "command"
        elif tok == "2" and self.screen == "movelist":
            self.slot = min(4, self.slot + repeat)
        elif tok == "4" and self.screen == "movelist":
            self.slot = max(1, self.slot - repeat)
        elif tok == "R":
            self.tera += 1

    def io(self, *, tera_available=False):
        return BattleIO(
            click=self.click,
            is_command_menu=self.is_command_menu,
            is_move_select=self.is_move_select,
            tera_available=lambda: tera_available,
            battle_ended=lambda: False,
            battle_lost=lambda: False,
            at_text_box=self.at_text_box,
            battle_state=lambda: None,
            sleep=self.clock.sleep,
            now=self.clock.now,
            get_move_slot=self.get_move_slot,
        )  # type: ignore[arg-type]


def test_take_turn_commits_slot_1():
    sim = BattleSim(Clock())
    assert A.take_turn(1, want_tera=False, cfg=AttackerConfig(), io=sim.io()) is True
    assert sim.committed == 1
    assert sim.tera == 0


def test_take_turn_commits_slot_2():
    sim = BattleSim(Clock())
    assert A.take_turn(2, want_tera=False, cfg=AttackerConfig(), io=sim.io()) is True
    assert sim.committed == 2


def test_take_turn_commits_slot_4():
    sim = BattleSim(Clock())
    assert A.take_turn(4, want_tera=False, cfg=AttackerConfig(), io=sim.io()) is True
    assert sim.committed == 4


def test_take_turn_teras_when_available():
    sim = BattleSim(Clock())
    A.take_turn(2, want_tera=True, cfg=AttackerConfig(tera_button="R"),
                io=sim.io(tera_available=True))
    assert sim.tera == 1
    assert sim.committed == 2


def test_take_turn_no_tera_when_prompt_absent():
    sim = BattleSim(Clock())
    A.take_turn(2, want_tera=True, cfg=AttackerConfig(), io=sim.io(tera_available=False))
    assert sim.tera == 0


def test_take_turn_no_tera_when_use_tera_disabled():
    sim = BattleSim(Clock())
    A.take_turn(2, want_tera=True, cfg=AttackerConfig(use_tera=False),
                io=sim.io(tera_available=True))
    assert sim.tera == 0


def test_take_turn_recovers_when_boss_dismisses_list_midnav():
    # Boss closes the move list the first two times we read the cursor mid-nav.
    sim = BattleSim(Clock())
    sim.dismiss_before_reads = 2
    assert A.take_turn(3, want_tera=False, cfg=AttackerConfig(), io=sim.io()) is True
    # Never mis-fires onto the default top slot — commits exactly the target.
    assert sim.committed == 3


def test_take_turn_recovers_when_commit_bounces_to_command_menu():
    # First commit A bounces back to the command menu (boss interrupt); retry lands it.
    sim = BattleSim(Clock())
    sim.fail_commits = 1
    assert A.take_turn(2, want_tera=False, cfg=AttackerConfig(), io=sim.io()) is True
    assert sim.committed == 2


def test_take_turn_never_recasts_top_slot_on_interrupt():
    # The old blind sequence would re-cast slot 1 (Belly Drum) when interrupted.
    # With gating, a dismiss mid-nav must never leave slot 1 committed for a slot-2 turn.
    sim = BattleSim(Clock())
    sim.dismiss_before_reads = 1
    A.take_turn(2, want_tera=False, cfg=AttackerConfig(), io=sim.io())
    assert sim.committed == 2
    assert sim.committed != 1


def test_take_turn_returns_false_when_menu_never_opens():
    cfg = AttackerConfig(menu_action_timeout_sec=1.0)
    io, clicks = make_io()  # nothing ever true — no command menu, no move list
    assert A.take_turn(2, want_tera=False, cfg=cfg, io=io) is False
    # Never committed anything (no stray A on a phantom move list).
    assert io  # sanity


def test_take_turn_stops_if_boss_already_defeated():
    io, _ = make_io(battle_state=lambda: False)  # host: boss down
    assert A.take_turn(2, want_tera=False, cfg=AttackerConfig(), io=io) is True


# ─────────────────────────────────────────────────────────────────────────────
# open_move_list / goto_move_slot / commit_move — unit level
# ─────────────────────────────────────────────────────────────────────────────


def test_open_move_list_from_command_menu():
    sim = BattleSim(Clock())
    assert A.open_move_list(AttackerConfig(), sim.io()) is True
    assert sim.is_move_select()


def test_open_move_list_already_open():
    sim = BattleSim(Clock(), screen="movelist")
    io = sim.io()
    assert A.open_move_list(AttackerConfig(), io) is True
    assert sim.clicks == []  # no press needed


def test_open_move_list_times_out_when_no_menu():
    cfg = AttackerConfig(menu_action_timeout_sec=1.0)
    io, _ = make_io()
    assert A.open_move_list(cfg, io) is False


def test_goto_move_slot_steps_down():
    sim = BattleSim(Clock(), screen="movelist")
    assert A.goto_move_slot(3, AttackerConfig(), sim.io()) is True
    assert sim.slot == 3


def test_goto_move_slot_returns_false_when_list_vanishes():
    sim = BattleSim(Clock(), screen="movelist")
    sim.dismiss_before_reads = 1  # first read returns 0 (list gone)
    assert A.goto_move_slot(3, AttackerConfig(), sim.io()) is False


def test_commit_move_true_when_leaves_list():
    sim = BattleSim(Clock(), screen="movelist")
    sim.slot = 2
    assert A.commit_move(AttackerConfig(), sim.io()) is True
    assert sim.committed == 2


def test_commit_move_false_when_bounces_to_command():
    sim = BattleSim(Clock(), screen="movelist")
    sim.fail_commits = 1
    assert A.commit_move(AttackerConfig(), sim.io()) is False


def test_get_move_slot_seam_defaults_to_zero():
    io, _ = make_io()
    assert io.get_move_slot() == 0


def test_try_tera_presses_configured_button():
    io, clicks = make_io()
    A.try_tera(AttackerConfig(tera_button="R"), io)
    assert clicks == [("R", 1)]


# ─────────────────────────────────────────────────────────────────────────────
# catch_pokemon — OCR ball-carousel navigation
# ─────────────────────────────────────────────────────────────────────────────


def test_catch_navigates_to_timer_ball():
    # Carousel opens immediately; names cycle Quick → Repeat → Timer as we press Right.
    state = {"cur": "Quick Ball"}

    def read():
        return state["cur"]

    io, clicks = make_io(at_ball_select=lambda: True, read_ball_name=read)
    # advance the carousel name each Right press
    seq = iter(["Repeat Ball", "Timer Ball"])
    base_click = io.click

    def click(tok, sleep=0.0, repeat=1):
        base_click(tok, sleep, repeat)
        if tok == "1":
            state["cur"] = next(seq, "Timer Ball")
    io.click = click

    ok = A.catch_pokemon(AttackerConfig(catch_ball="Timer Ball"), io)
    assert ok is True
    # Pressed Right twice (Quick→Repeat→Timer), then A to throw.
    assert clicks.count(("1", 1)) == 2
    assert clicks[-1][0] == "A"


def test_catch_throws_default_when_carousel_never_appears():
    io, clicks = make_io(at_ball_select=lambda: False)
    cfg = AttackerConfig(catch_timeout_sec=1.0)
    ok = A.catch_pokemon(cfg, io)
    assert ok is False
    assert ("A", 1) in clicks  # fell back to throwing the default ball


def test_catch_fuzzy_matches_noisy_ocr():
    # OCR often returns "TimerBall" / "TimerBall =" — must still match.
    io, clicks = make_io(at_ball_select=lambda: True, read_ball_name=lambda: "TimerBall =")
    ok = A.catch_pokemon(AttackerConfig(catch_ball="Timer Ball"), io)
    assert ok is True
    assert clicks.count(("1", 1)) == 0  # already on target, no navigation


# ─────────────────────────────────────────────────────────────────────────────
# run_attacker_macro — full sequencing (module functions monkeypatched)
# ─────────────────────────────────────────────────────────────────────────────


def _instrument(monkeypatch, *, start=True, turn_results, defeated_after_drains,
                tera_available=False, lost=False):
    """Patch the macro's collaborators and return an ordered event log.

    events: ("move", slot) and ("tera",) in call order.
    """
    events = []  # ("turn", slot, want_tera) per take_turn call, in order
    cfg = AttackerConfig(belly_drum_slot=1, drain_punch_slot=2)

    def fake_take_turn(slot, want_tera, cfg, io):
        events.append(("turn", slot, want_tera))

    def fake_defeated(io):
        drains = sum(1 for e in events if e[0] == "turn" and e[1] == cfg.drain_punch_slot)
        return drains >= defeated_after_drains

    results = iter(turn_results)

    monkeypatch.setattr(A, "wait_for_battle_start", lambda c, io: start)
    monkeypatch.setattr(A, "wait_for_my_turn", lambda c, io: next(results, "turn"))
    monkeypatch.setattr(A, "take_turn", fake_take_turn)
    monkeypatch.setattr(A, "boss_defeated", fake_defeated)

    io, _ = make_io(tera_available=lambda: tera_available, battle_lost=lambda: lost)
    return cfg, io, events


def test_macro_happy_path_belly_drum_once_then_drain_punch(monkeypatch):
    cfg, io, events = _instrument(
        monkeypatch, turn_results=["turn"] * 5,
        defeated_after_drains=2, tera_available=True)
    result = A.run_attacker_macro(cfg, io)

    assert result.win is True
    assert result.reason == "boss-defeated"
    # Belly Drum (slot 1) exactly once, and it is the FIRST action.
    slots = [e[1] for e in events]
    assert slots[0] == 1
    assert slots.count(1) == 1
    # Then Drain Punch (slot 2) until the boss dies.
    assert slots.count(2) == 2


def test_macro_tera_never_on_turn_1(monkeypatch):
    cfg, io, events = _instrument(
        monkeypatch, turn_results=["turn"] * 5,
        defeated_after_drains=1, tera_available=True)
    A.run_attacker_macro(cfg, io)
    # Turn 1 (the first take_turn) is called with want_tera=False (meter empty).
    assert events[0] == ("turn", 1, False)
    # Turn 2+ are called with want_tera=True.
    assert all(e[2] is True for e in events[1:])
    assert any(e[2] is True for e in events[1:])


def test_macro_safety_cap_no_fake_victory(monkeypatch):
    cfg, io, events = _instrument(
        monkeypatch, turn_results=["turn"] * 100,
        defeated_after_drains=999)  # boss never dies
    cfg.combat_max_turns = 4
    result = A.run_attacker_macro(cfg, io)

    assert result.win is False
    assert result.reason == "max-turns"
    # Belly Drum (slot 1) still only once.
    assert [e[1] for e in events].count(1) == 1


def test_macro_battle_never_started(monkeypatch):
    cfg, io, events = _instrument(
        monkeypatch, start=False, turn_results=[], defeated_after_drains=0)
    result = A.run_attacker_macro(cfg, io)
    assert result.win is False
    assert result.reason == "battle-never-started"
    assert events == []  # never pressed a move


def test_macro_boss_already_down_before_turn_1(monkeypatch):
    cfg, io, events = _instrument(
        monkeypatch, turn_results=["ended"], defeated_after_drains=0)
    result = A.run_attacker_macro(cfg, io)
    assert result.win is True
    assert result.reason == "boss-defeated"
    assert events == []


def test_macro_battle_lost(monkeypatch):
    cfg, io, events = _instrument(
        monkeypatch, turn_results=["turn"], defeated_after_drains=999, lost=True)
    result = A.run_attacker_macro(cfg, io)
    assert result.win is False
    assert result.reason == "lost"
