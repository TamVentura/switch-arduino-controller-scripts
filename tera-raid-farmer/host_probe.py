"""
host_probe.py — micro harness to manually validate the Switch 1 HOST alone.

It does ONE thing: connect to the host's /ws push and, every time the host opens
a NEW raid lobby, print the lobby code and wait for you to press Enter. Pressing
Enter sends the same `start_battle` signal the real Switch 2 farmer sends (POST
/api/action) — so you can join the lobby by hand on Switch 2, tell the host you're
ready, and watch it run — without running main.py's vision/controller stack.

No capture device, no Arduino, no OCR: just the WebSocket link and the ready POST.

Usage:
    python host_probe.py                       # uses config.json host_api_url
    python host_probe.py http://127.0.0.1:8080 # or pass the host URL explicitly
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

from colorama import Fore, Style

from host_link import HostLink


def _load_host_url() -> str:
    if len(sys.argv) >= 2 and sys.argv[1].strip():
        return sys.argv[1].strip()
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f).get("host_api_url", "http://127.0.0.1:8080")
    return "http://127.0.0.1:8080"


def signal_host_ready(base_url: str) -> bool:
    """Tell the Switch 1 host the farmer is ready → it starts the battle. Same
    one-shot POST /api/action {"action": "start_battle"} that main.py sends."""
    url = f"{base_url.rstrip('/')}/api/action"
    payload = json.dumps({"action": "start_battle"}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "HostProbe/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            return resp.status == 200
    except Exception as exc:
        print(f"{Fore.RED}[READY]{Style.RESET_ALL} POST failed: {exc}")
        return False


def main() -> None:
    host_api = _load_host_url()

    def _hl_log(level, msg):
        color = Fore.YELLOW if level == "WARNING" else Fore.LIGHTBLUE_EX
        print(f"{color}[HOST]{Style.RESET_ALL} {msg}")

    host_link = HostLink(host_api, on_log=_hl_log).start()

    print(Fore.LIGHTCYAN_EX + f"""
=============================================================================
  HOST PROBE — manual Switch 1 host validation (no capture / no controller)
=============================================================================
  Host push (WebSocket): {host_link.ws_url}
  Waiting for the host to open a raid lobby...
=============================================================================
""" + Style.RESET_ALL)

    try:
        while True:
            # Block on the host push (local event wait, not polling). Short
            # timeout only so Ctrl+C stays responsive between waits.
            code = host_link.wait_for_new_lobby(timeout=1.0)
            if not code:
                continue

            print(f"\n{Fore.LIGHTGREEN_EX}🎯 [LOBBY OPEN]{Style.RESET_ALL}  "
                  f"code: [{Fore.YELLOW}{code}{Style.RESET_ALL}]")
            raid = host_link.latest_lobby().get("raid") or {}
            if raid:
                print(f"   ⭐ {raid.get('stars')}★ {raid.get('species_name')} "
                      f"({raid.get('form_name')}) | Shiny: {raid.get('is_shiny')}")

            input(f"{Fore.CYAN}   ↵ Join on Switch 2, then press Enter when ready "
                  f"(signals the host to start)...{Style.RESET_ALL}")
            ok = signal_host_ready(host_api)
            print(f"{Fore.GREEN if ok else Fore.RED}[READY]{Style.RESET_ALL} "
                  f"start_battle sent: {ok}")
            print(f"{Fore.LIGHTBLACK_EX}   waiting for the next lobby...{Style.RESET_ALL}")
    except KeyboardInterrupt:
        print(f"\n{Fore.LIGHTBLACK_EX}[PROBE]{Style.RESET_ALL} stopping.")
    finally:
        host_link.stop()


if __name__ == "__main__":
    main()
