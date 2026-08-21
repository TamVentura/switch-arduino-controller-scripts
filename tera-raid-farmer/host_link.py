"""
host_link.py — event-driven link to the Switch 1 host over its WebSocket push.

The host (auto-raid-switch1) exposes GET /ws (src/realtime.py): on connect it
sends the full `state` document, then pushes a fresh copy on every FSM state
change. This replaces ALL HTTP polling the farmer used to do:

  - lobby detection   (was polling GET /api/lobby)
  - battle-end signal (was polling GET /api/battle_state)

Both now come from the pushed document:
  - state["lobby"] = {"is_open": bool, "code": str|null, "raid": {...}|null}
  - state["fsm_state"] = FSMState value; "IN_BATTLE" while the raid battle runs,
    transitioning to "BATTLE_REWARDS"/etc. the instant the boss is down.

A background thread runs an asyncio WebSocket client (auto-reconnecting) and
keeps the latest document in thread-safe state. The synchronous farmer main loop
and the attacker macro read it without ever touching the network:

  - wait_for_new_lobby(timeout) -> code|None   (blocks on a local event)
  - in_battle() -> bool|None                   (True=IN_BATTLE, False=not,
                                                 None=WS down/unknown -> vision)

Outbound *commands* (start_battle, caught) remain one-shot HTTP POSTs — those are
single events, not polling, and the /ws channel is read-only push.
"""

from __future__ import annotations

import asyncio
import json
import threading
import urllib.parse
from typing import Any, Callable, Dict, Optional


class HostLink:
    """Background WebSocket client that mirrors the host's pushed state."""

    def __init__(self, host_api_url: str, on_log: Optional[Callable[[str, str], None]] = None,
                 reconnect_backoff_sec: float = 2.0):
        self._ws_url = self._to_ws_url(host_api_url)
        self._log = on_log or (lambda level, msg: None)
        self._reconnect_backoff = reconnect_backoff_sec

        self._lock = threading.Lock()
        self._state: Dict[str, Any] = {}
        self._connected = False

        self._new_lobby = threading.Event()
        self._pending_code: Optional[str] = None
        self._last_code: Optional[str] = None

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ── URL ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _to_ws_url(http_url: str) -> str:
        u = urllib.parse.urlparse(http_url)
        scheme = "wss" if u.scheme == "https" else "ws"
        netloc = u.netloc or u.path  # tolerate "host:port" without a scheme
        return f"{scheme}://{netloc}/ws"

    @property
    def ws_url(self) -> str:
        return self._ws_url

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> "HostLink":
        if self._thread is not None:
            return self
        self._thread = threading.Thread(target=self._run, name="HostLinkWS", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        loop = self._loop
        if loop is not None:
            try:
                loop.call_soon_threadsafe(lambda: None)
            except Exception:
                pass

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._consume())
        except Exception as e:  # pragma: no cover — background thread guard
            self._log("WARNING", f"HostLink loop exited: {e}")
        finally:
            self._set_connected(False)

    async def _consume(self) -> None:
        import websockets  # imported lazily so the module imports without the dep

        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    self._ws_url, open_timeout=5, ping_interval=20, ping_timeout=20
                ) as ws:
                    self._set_connected(True)
                    self._log("INFO", f"HostLink connected: {self._ws_url}")
                    async for raw in ws:
                        if self._stop.is_set():
                            break
                        try:
                            doc = json.loads(raw)
                        except Exception:
                            continue
                        self._on_state(doc)
            except Exception as e:
                self._log("WARNING", f"HostLink disconnected ({e}); reconnecting…")
            self._set_connected(False)
            if self._stop.is_set():
                break
            # Reconnect backoff — re-establishing a dropped push channel, not
            # polling for data.
            await asyncio.sleep(self._reconnect_backoff)

    # ── State intake ─────────────────────────────────────────────────────────

    def _set_connected(self, value: bool) -> None:
        with self._lock:
            self._connected = value

    def _on_state(self, doc: Dict[str, Any]) -> None:
        with self._lock:
            self._state = doc
            lobby = doc.get("lobby") or {}
            code = lobby.get("code")
            is_open = bool(lobby.get("is_open", False))
            if is_open and code and code != self._last_code:
                self._pending_code = code
                self._last_code = code
                self._new_lobby.set()

    # ── Reads for the farmer (no network) ────────────────────────────────────

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._connected

    @property
    def fsm_state(self) -> Optional[str]:
        with self._lock:
            return self._state.get("fsm_state") or self._state.get("state")

    def latest_lobby(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._state.get("lobby") or {})

    def in_battle(self) -> Optional[bool]:
        """Host's authoritative in-battle signal from the pushed FSM state.

        True  = host FSM is IN_BATTLE (raid battle running)
        False = host FSM is known and NOT IN_BATTLE (boss down / pre-battle)
        None  = WS not connected / no state yet → caller falls back to vision
        """
        with self._lock:
            if not self._connected or not self._state:
                return None
            fs = self._state.get("fsm_state") or self._state.get("state")
        if fs is None:
            return None
        return fs == "IN_BATTLE"

    def wait_for_new_lobby(self, timeout: float) -> Optional[str]:
        """Block up to `timeout`s for the host to push a NEW open lobby. Returns
        the 4-char code once, or None on timeout (so the caller can re-check its
        own stop flag between waits — a local event wait, not host polling)."""
        if self._new_lobby.wait(timeout):
            with self._lock:
                code = self._pending_code
                self._pending_code = None
                self._new_lobby.clear()
            return code
        return None
