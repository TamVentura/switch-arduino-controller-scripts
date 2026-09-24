"""
Tests for storing macros and running them (utils/macro_runner.py).

The runner is what the terminal menu and the web page both drive, so the rules
that matter are here: one macro at a time, the pad muted while it runs, and
nothing left pressed when it ends — however it ends.  A fake controller stands
in for the Arduino; the fact that the Switch obeys is checked live.
"""

import os
import sys
import time

import pytest

_HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..")))

import macro_runner as R  # noqa: E402
from macros import MacroError  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Fakes
# ─────────────────────────────────────────────────────────────────────────────


class FakeIO:
    """Stands in for controller.py."""

    def __init__(self):
        self.calls: list[tuple] = []

    def hold(self, key):
        self.calls.append(("hold", key))

    def release(self, key):
        self.calls.append(("release", key))

    def set_left_stick(self, x, y):
        self.calls.append(("left", x, y))

    def set_right_stick(self, x, y):
        self.calls.append(("right", x, y))

    def keys(self, op):
        return [c[1] for c in self.calls if c[0] == op]


class FakePad:
    """Stands in for xbox_passthrough."""

    def __init__(self):
        self.events: list[str] = []
        self.suspended = False

    def suspend(self):
        self.suspended = True
        self.events.append("suspend")

    def resume(self):
        self.suspended = False
        self.events.append("resume")

    def is_suspended(self):
        return self.suspended


@pytest.fixture
def store(tmp_path):
    return R.MacroStore(tmp_path)


@pytest.fixture
def runner(store):
    io, pad = FakeIO(), FakePad()
    r = R.MacroRunner(store, io=io, pad=pad)
    yield r, io, pad
    r.stop()


def wait_until(predicate, timeout=2.0):
    """Poll until predicate() is true; return whether it got there."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


# ─────────────────────────────────────────────────────────────────────────────
# Storage
# ─────────────────────────────────────────────────────────────────────────────


def test_a_saved_macro_can_be_read_back(store):
    store.save("passear", "click A\nwait 1")
    assert store.load("passear") == "click A\nwait 1"


def test_names_come_back_sorted(store):
    store.save("zebra", "click A")
    store.save("abelha", "click B")
    assert store.names() == ["abelha", "zebra"]


def test_saving_an_invalid_macro_is_refused(store):
    with pytest.raises(MacroError):
        store.save("partida", "click Z")
    assert store.names() == []


def test_deleting_removes_the_macro(store):
    store.save("temporaria", "click A")
    store.delete("temporaria")
    assert store.names() == []


def test_a_name_cannot_escape_the_macro_directory(store):
    # The web server takes names straight from the URL.
    with pytest.raises(ValueError):
        store.save("../../switch", "click A")


def test_an_empty_name_is_refused(store):
    with pytest.raises(ValueError):
        store.save("   ", "click A")


# ─────────────────────────────────────────────────────────────────────────────
# Running
# ─────────────────────────────────────────────────────────────────────────────


def test_running_sends_the_commands_in_order(runner):
    r, io, _pad = runner
    r.store.save("abrir", "click A\nhold B")
    r.start("abrir")

    assert wait_until(lambda: not r.is_running())
    assert io.calls[:3] == [("hold", "A"), ("release", "A"), ("hold", "B")]


def test_a_stick_command_reaches_the_right_stick(runner):
    r, io, _pad = runner
    r.store.save("olhar", "stick right 1 0")
    r.start("olhar")

    assert wait_until(lambda: not r.is_running())
    assert ("right", 1.0, 0.0) in io.calls


def test_the_pad_is_muted_while_the_macro_runs_and_freed_after(runner):
    r, _io, pad = runner
    r.store.save("demorada", "wait 5")
    r.start("demorada")

    assert wait_until(lambda: pad.is_suspended())
    r.stop()
    assert wait_until(lambda: not pad.is_suspended())


def test_stopping_interrupts_a_long_wait_promptly(runner):
    r, _io, _pad = runner
    r.store.save("eterna", "wait 30")
    r.start("eterna")
    assert wait_until(lambda: r.is_running())

    started = time.monotonic()
    r.stop()

    assert wait_until(lambda: not r.is_running())
    assert time.monotonic() - started < 1.0


def test_stopping_releases_whatever_the_macro_was_holding(runner):
    r, io, _pad = runner
    r.store.save("segurar", "hold B\nwait 30")
    r.start("segurar")
    assert wait_until(lambda: ("hold", "B") in io.calls)

    r.stop()

    assert wait_until(lambda: ("release", "B") in io.calls)


def test_stopping_centres_the_sticks(runner):
    r, io, _pad = runner
    r.store.save("correr", "stick left 0 -1\nwait 30")
    r.start("correr")
    assert wait_until(lambda: ("left", 0.0, -1.0) in io.calls)

    r.stop()

    assert wait_until(lambda: ("left", 0.0, 0.0) in io.calls)


def test_a_macro_that_ends_by_itself_releases_what_it_held(runner):
    r, io, _pad = runner
    r.store.save("esquecida", "hold B")
    r.start("esquecida")

    assert wait_until(lambda: not r.is_running())
    assert ("release", "B") in io.calls


# ─────────────────────────────────────────────────────────────────────────────
# Looping and toggling
# ─────────────────────────────────────────────────────────────────────────────


def test_a_looping_macro_repeats_until_stopped(runner):
    r, io, _pad = runner
    r.store.save("insistente", "click A sleep=0")
    r.start("insistente", loop=True)

    assert wait_until(lambda: len(io.keys("hold")) >= 3)
    r.stop()
    assert wait_until(lambda: not r.is_running())


def test_a_run_once_macro_clears_itself_when_it_ends(runner):
    r, _io, _pad = runner
    r.store.save("curta", "click A sleep=0")
    r.start("curta")

    assert wait_until(lambda: r.status() is None)


def test_status_names_the_macro_and_its_mode(runner):
    r, _io, _pad = runner
    r.store.save("demorada", "wait 30")
    r.start("demorada", loop=True)

    assert wait_until(lambda: r.status() is not None)
    status = r.status()
    assert status["name"] == "demorada"
    assert status["loop"] is True


def test_status_counts_the_laps_of_a_loop(runner):
    r, _io, _pad = runner
    r.store.save("voltas", "click A sleep=0")
    r.start("voltas", loop=True)

    assert wait_until(lambda: (r.status() or {}).get("lap", 0) >= 2)


def test_toggling_a_running_macro_stops_it(runner):
    r, _io, _pad = runner
    r.store.save("demorada", "wait 30")
    r.toggle("demorada")
    assert wait_until(lambda: r.is_running())

    r.toggle("demorada")

    assert wait_until(lambda: not r.is_running())


def test_toggling_another_macro_replaces_the_running_one(runner):
    r, _io, _pad = runner
    r.store.save("primeira", "wait 30")
    r.store.save("segunda", "wait 30")
    r.toggle("primeira")
    assert wait_until(lambda: r.is_running())

    r.toggle("segunda")

    assert wait_until(lambda: (r.status() or {}).get("name") == "segunda")


def test_starting_an_invalid_macro_raises_before_touching_the_pad(runner):
    r, io, pad = runner
    path = r.store.path_for("estragada")
    path.write_text("click A\nclick Z\n", encoding="utf-8")  # bypassing save()

    with pytest.raises(MacroError):
        r.start("estragada")

    assert io.calls == []
    assert pad.events == []


def test_toggling_the_same_macro_in_the_other_mode_switches_mode(runner):
    r, _io, _pad = runner
    r.store.save("demorada", "wait 30")
    r.toggle("demorada")  # uma vez
    assert wait_until(lambda: r.is_running())

    r.toggle("demorada", loop=True)

    # Pedir loop a uma macro que corre uma vez e passa-la a loop, nao para-la:
    # so o mesmo gesto exacto e que desliga.
    assert wait_until(lambda: (r.status() or {}).get("loop") is True)


def test_toggling_a_looping_macro_the_same_way_stops_it(runner):
    r, _io, _pad = runner
    r.store.save("demorada", "wait 30")
    r.toggle("demorada", loop=True)
    assert wait_until(lambda: r.is_running())

    r.toggle("demorada", loop=True)

    assert wait_until(lambda: not r.is_running())
