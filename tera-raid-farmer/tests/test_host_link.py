"""
Unit tests for HostLink's pure state-intake logic — the parts that turn a pushed
`state` document into the farmer's lobby / in-battle signals. The WebSocket
transport itself (asyncio thread, reconnect) is not exercised here.
"""

import os
import sys

_HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..")))

from host_link import HostLink  # noqa: E402


def _link():
    return HostLink("http://127.0.0.1:8080")


def _state(fsm_state=None, is_open=False, code=None):
    return {
        "type": "state",
        "fsm_state": fsm_state,
        "lobby": {"is_open": is_open, "code": code, "raid": None},
    }


# ── URL conversion ───────────────────────────────────────────────────────────


def test_ws_url_from_http():
    assert HostLink("http://127.0.0.1:8080").ws_url == "ws://127.0.0.1:8080/ws"


def test_ws_url_from_https():
    assert HostLink("https://host.local:9000").ws_url == "wss://host.local:9000/ws"


def test_ws_url_without_scheme():
    assert HostLink("192.168.1.50:8080").ws_url == "ws://192.168.1.50:8080/ws"


# ── in_battle mapping ────────────────────────────────────────────────────────


def test_in_battle_none_when_disconnected():
    link = _link()
    link._on_state(_state(fsm_state="IN_BATTLE"))
    # never connected → unknown
    assert link.in_battle() is None


def test_in_battle_true_in_battle_state():
    link = _link()
    link._set_connected(True)
    link._on_state(_state(fsm_state="IN_BATTLE"))
    assert link.in_battle() is True


def test_in_battle_false_after_battle():
    link = _link()
    link._set_connected(True)
    link._on_state(_state(fsm_state="BATTLE_REWARDS"))
    assert link.in_battle() is False


def test_in_battle_false_pre_battle_lobby():
    link = _link()
    link._set_connected(True)
    link._on_state(_state(fsm_state="WAITING_FOR_FARMER", is_open=True, code="1234"))
    assert link.in_battle() is False


def test_in_battle_none_when_no_state_yet():
    link = _link()
    link._set_connected(True)
    assert link.in_battle() is None


def test_in_battle_reads_state_field_fallback():
    # If the doc only carries "state" (not "fsm_state"), use it.
    link = _link()
    link._set_connected(True)
    link._on_state({"state": "IN_BATTLE", "lobby": {}})
    assert link.in_battle() is True


# ── new-lobby edge detection ─────────────────────────────────────────────────


def test_new_lobby_fires_once_per_code():
    link = _link()
    link._on_state(_state(is_open=True, code="ABCD"))
    assert link.wait_for_new_lobby(timeout=0.01) == "ABCD"
    # Same code pushed again → no new event.
    link._on_state(_state(is_open=True, code="ABCD"))
    assert link.wait_for_new_lobby(timeout=0.01) is None


def test_new_lobby_fires_again_for_a_different_code():
    link = _link()
    link._on_state(_state(is_open=True, code="ABCD"))
    assert link.wait_for_new_lobby(timeout=0.01) == "ABCD"
    link._on_state(_state(is_open=True, code="WXYZ"))
    assert link.wait_for_new_lobby(timeout=0.01) == "WXYZ"


def test_new_lobby_ignores_closed_lobby():
    link = _link()
    link._on_state(_state(is_open=False, code="ABCD"))
    assert link.wait_for_new_lobby(timeout=0.01) is None


def test_new_lobby_timeout_returns_none():
    link = _link()
    assert link.wait_for_new_lobby(timeout=0.01) is None


def test_latest_lobby_exposes_pushed_block():
    link = _link()
    link._on_state(_state(is_open=True, code="ABCD"))
    lobby = link.latest_lobby()
    assert lobby["is_open"] is True and lobby["code"] == "ABCD"
