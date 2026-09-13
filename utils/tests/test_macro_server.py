"""
Tests for the macro web channel (utils/macro_server.py).

A real server on an ephemeral port, driven over real HTTP, with a fake
controller underneath.  The page is the other hand on the same runner as the
terminal menu, so what is checked here is that both hands agree — and that a
name arriving from a URL cannot wander outside the macro directory.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

import pytest

_HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..")))

import macro_runner as R  # noqa: E402
import macro_server as S  # noqa: E402
from test_macro_runner import FakeIO, FakePad, wait_until  # noqa: E402


@pytest.fixture
def server(tmp_path):
    runner = R.MacroRunner(R.MacroStore(tmp_path), io=FakeIO(), pad=FakePad())
    srv = S.MacroServer(runner, port=0)
    srv.start()
    yield srv, runner
    srv.stop()
    runner.stop()


def call(server, path, method="GET", body=None):
    """Do a request, returning (status, text)."""
    request = urllib.request.Request(
        f"{server.url}{path}",
        method=method,
        data=body.encode("utf-8") if body is not None else None,
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def get_json(server, path):
    status, text = call(server, path)
    assert status == 200, text
    return json.loads(text)


# ─────────────────────────────────────────────────────────────────────────────
# The page
# ─────────────────────────────────────────────────────────────────────────────


def test_the_root_serves_the_page(server):
    srv, _runner = server
    status, text = call(srv, "/")
    assert status == 200
    assert "<html" in text.lower()


def test_an_unknown_path_is_a_404(server):
    srv, _runner = server
    status, _text = call(srv, "/nao-existe")
    assert status == 404


# ─────────────────────────────────────────────────────────────────────────────
# Listing, reading, writing
# ─────────────────────────────────────────────────────────────────────────────


def test_the_listing_shows_the_stored_macros(server):
    srv, runner = server
    runner.store.save("abrir", "click A")
    body = get_json(srv, "/api/macros")
    assert body["names"] == ["abrir"]
    assert body["status"] is None


def test_a_macro_written_over_http_can_be_read_back(server):
    srv, runner = server
    status, _text = call(srv, "/api/macro/nova", method="PUT", body="click A\nwait 1")
    assert status == 200
    assert runner.store.load("nova") == "click A\nwait 1"
    assert get_json(srv, "/api/macro/nova")["text"] == "click A\nwait 1"


def test_writing_a_broken_macro_is_refused_with_the_reason(server):
    srv, runner = server
    status, text = call(srv, "/api/macro/partida", method="PUT", body="click Z")
    assert status == 400
    assert "linha 1" in text
    assert runner.store.names() == []


def test_reading_a_macro_that_does_not_exist_is_a_404(server):
    srv, _runner = server
    status, _text = call(srv, "/api/macro/fantasma")
    assert status == 404


def test_deleting_over_http_removes_it(server):
    srv, runner = server
    runner.store.save("temporaria", "click A")
    status, _text = call(srv, "/api/macro/temporaria", method="DELETE")
    assert status == 200
    assert runner.store.names() == []


def test_a_name_from_the_url_cannot_escape_the_directory(server):
    srv, _runner = server
    status, _text = call(srv, "/api/macro/..%2F..%2Fswitch", method="PUT", body="click A")
    assert status == 400


# ─────────────────────────────────────────────────────────────────────────────
# Running from the page
# ─────────────────────────────────────────────────────────────────────────────


def test_toggling_over_http_starts_and_stops(server):
    srv, runner = server
    runner.store.save("demorada", "wait 30")

    call(srv, "/api/macro/demorada/toggle", method="POST")
    assert wait_until(lambda: runner.is_running())

    call(srv, "/api/macro/demorada/toggle", method="POST")
    assert wait_until(lambda: not runner.is_running())


def test_the_loop_flag_comes_through_the_query_string(server):
    srv, runner = server
    runner.store.save("demorada", "wait 30")

    call(srv, "/api/macro/demorada/toggle?loop=1", method="POST")

    assert wait_until(lambda: (runner.status() or {}).get("loop") is True)


def test_the_status_endpoint_reports_what_is_running(server):
    srv, runner = server
    runner.store.save("demorada", "wait 30")
    runner.start("demorada", loop=True)
    assert wait_until(lambda: runner.is_running())

    status = get_json(srv, "/api/macros")["status"]

    assert status["name"] == "demorada"
    assert status["loop"] is True


def test_stopping_over_http_stops_what_the_terminal_started(server):
    srv, runner = server
    runner.store.save("demorada", "wait 30")
    runner.start("demorada")  # as if from the menu
    assert wait_until(lambda: runner.is_running())

    call(srv, "/api/stop", method="POST")

    assert wait_until(lambda: not runner.is_running())


def test_running_a_macro_that_no_longer_parses_answers_with_the_error(server):
    srv, runner = server
    runner.store.path_for("estragada").write_text("click Z\n", encoding="utf-8")

    status, text = call(srv, "/api/macro/estragada/toggle", method="POST")

    assert status == 400
    assert "linha 1" in text
    assert not runner.is_running()
