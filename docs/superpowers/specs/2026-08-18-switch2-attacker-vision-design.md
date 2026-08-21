# Design — Switch 2 vision-driven Iron Hands attacker

Date: 2026-08-18
Scope: `tera-raid-farmer/` (Switch 2 farmer) + shared battle detectors in `utils/pokemon_sv.py`.
Do **not** touch `C:\Projects\auto-raid-switch1` (host, separate session).

## 1. Context

Two-console Tera Raid farm:

- **Switch 1 = HOST.** Has sys-botbase, reads RAM, hosts the raid, runs the FSM in
  `auto-raid-switch1`. Now plays **support (Screech)**.
- **Switch 2 = ATTACKER.** Arduino/Joy-Con + capture-card vision, **no sys-botbase**. Runs
  `tera-raid-farmer/main.py`. Now plays **Iron Hands attacker** (Lv100 Adamant, Tera Fighting,
  Belly Drum in move-slot 1, Drain Punch in move-slot 2).

The current combat block on Switch 2 (`use_screech_on_boss` / `handle_battle_and_rewards`) is
best-effort `sleep()`-driven and plays Screech. This work replaces it with a **vision-driven
attacker macro** that reacts to the on-screen state instead of sleeping blind. The lobby handshake
and the reward/catch tail are kept.

### Host contract already in place (verified in `auto-raid-switch1`)

- HTTP REST is still served by `src/api_server.py`: `/api/lobby`, `/api/action`,
  `/api/battle_state`, etc. — the farmer's existing HTTP polling still works.
- `GET /api/battle_state` (`api_server.py:328`) is **purpose-built for this attacker**: returns
  `{"in_battle": true|false, "flag": <int>, "fsm_state": ...}` derived from the host's RAM combat
  flag `0x047D2020 == 0x02`; on read failure returns `{"in_battle": null, "error": ...}` so the
  caller can fall back to vision. This is the **authoritative stop signal**.
- WebSockets (`src/realtime.py`, wired in `auto-raid-switch1/main.py`) push a `state` document over
  `GET /ws` that **replaced the dashboard's** `/api/status` + `/api/lobby` polling. The push doc
  carries a `lobby` block but **not** the battle flag. The farmer side currently has **no** WS
  client (grep-confirmed: zero WS references anywhere under `tera-raid-farmer/`).

**Consequence for this work (updated — no polling):** per the user's "não quero pollings de todo",
all host HTTP polling is removed. The farmer is a **`/ws` push client** (`host_link.py`): the host
pushes the lobby the instant it opens (`state["lobby"]`) and the FSM state the instant it changes
(`state["fsm_state"]`; `IN_BATTLE` while the raid runs, `BATTLE_REWARDS`/etc. once the boss is down —
the hub pushes on every `fsm.on_state_change`). So:

- **Lobby** ← `/ws` push (was `GET /api/lobby` polling).
- **Battle stop** ← host's pushed `fsm_state` leaving `IN_BATTLE` (was `GET /api/battle_state`
  polling); vision `battle_ended()` remains the fallback when the WS is down.
- **Outbound commands** (`start_battle`, `caught`) stay one-shot HTTP POSTs — single events, not
  polling; the `/ws` channel is read-only push.
- Local **vision** frame-reading is not network polling and is unchanged.

`websockets` (16.0) is already installed; a background asyncio thread maintains the connection and
mirrors the latest document into thread-safe state.

## 2. Combat contract (mirror of the host macro, already validated on Switch 1)

The attacker must win with real damage (HP pokes do not work in a hosted raid). Exact sequence:

1. **Turn 1: Belly Drum** (`belly_drum_slot`, default 1). **Once only** — sets +6 Atk; repeating
   would only cut HP.
2. **Turn 2+: Drain Punch** (`drain_punch_slot`, default 2), **looping until the boss dies**.
   Drain Punch heals, so repeating indefinitely is safe.
3. **Tera (Fighting)** if `use_tera` (default true): **retry every turn from turn 2** until it
   takes. Terastal is a one-shot transform; re-pressing after it is already active is a no-op, so
   retrying is safe. **Never on turn 1** (meter empty).
4. **Safety cap** (`combat_max_turns`, default 50): only to avoid an infinite loop if end-detection
   fails. If reached while still in battle → log **WARNING** ("boss not confirmed dead"); **do not**
   fake victory.

## 3. Architecture & file layout

Split follows the "reusable → utils, raid-specific → folder" rule.

### 3a. `utils/pokemon_sv.py` (shared, reusable for future SV battle scripts)

Add battle-state **detectors** following the exact pattern of the existing `get_mode()` detectors
(4K crop via `capture.get_frame_region` → `template_matcher.find_all(region, png, threshold, scales)`
or `capture.region_is_mostly_color` / `region_color_ratio`; `ocr.read_field` where text is clearer):

- `is_command_menu() -> bool` — the "Fight / Bag / Pokémon / Run" command menu is open (= my turn).
- `is_move_select() -> bool` — the 4-move list is open (distinct from the command menu).
- `tera_available() -> bool` — the Terastal orb/prompt is lit (meter full).
- `battle_ended() -> bool` — raid result/reward screen **or** return to overworld
  (`_is_minimap_in_corner()`), i.e. the boss is down / raid over.
- `battle_lost() -> bool` — defeat/timeout (KO'd, "blown out of the raid", timer at zero).

Wire `Mode.IN_BATTLE` into `get_mode()` as the **last branch** (immediately before the final
`return "error"`), returning `Mode.IN_BATTLE` when `is_command_menu()` or `is_move_select()`. Placed
last so it can never shadow the modes other scripts rely on (`enter_tera_raid_lobby` calls
`get_mode()`), and gated by the calibration flag below so it stays inert until calibrated.

### 3b. `tera-raid-farmer/attacker.py` (new, raid-specific)

- `run_attacker_macro(cfg) -> AttackerResult` — the full macro (§4).
- `select_move(slot, delay)` — navigate the Fight menu to a 1-indexed move slot and confirm.
- `try_tera(button)` — press the Tera button (idempotent).
- `boss_defeated(io) -> bool` — host authoritative when known: `io.battle_state()` (backed by
  `HostLink.in_battle` from the `/ws` push) is `False` → defeated; on `None` (WS down) fall back to
  vision `battle_ended()`. A new `host_link.py` module owns the WS client and the `in_battle` signal.
- `wait_for_my_turn(timeout) -> "turn" | "ended" | "timeout"` — the reactive poll loop (§4).

### 3c. `tera-raid-farmer/main.py`

- Delete `use_screech_on_boss`.
- Rewrite `handle_battle_and_rewards` to call `run_attacker_macro(...)` for the combat, then keep
  the existing catch/reward-mash tail unchanged.
- Orchestration loop is now event-driven: `host_link.wait_for_new_lobby()` (blocks on the `/ws`
  push) → `enter_tera_raid_lobby` → `signal_host_ready` (one-shot POST) → combat → rewards.

## 4. Turn loop (reactive, no fixed sleeps)

```
boss_defeated(io):                      # host push authoritative, vision fallback
    flag = io.battle_state()            # HostLink.in_battle: True/False/None
    if flag is not None: return flag is False
    return battle_ended()

wait_for_my_turn(timeout):
    loop until deadline:
        if boss_defeated(io):                     return "ended"
        if is_command_menu():                     return "turn"
        if at_text_box():  click("A")   # advance dialogue — menu-safe
        else:              short poll sleep
    return "timeout"

run_attacker_macro:
    if not wait_for_battle_start(): return battle-never-started   # arm end-detection
    # Turn 1 — Belly Drum, once
    r = wait_for_my_turn(); if r == "ended": return WIN; if battle_lost(): return LOST
    select_move(belly_drum_slot)        # (on turn-1 timeout: re-check, then continue)
    turn = 2
    # Turn 2+ — Drain Punch loop
    while turn <= combat_max_turns:
        if boss_defeated(io): return WIN
        if battle_lost(): return LOST
        r = wait_for_my_turn()
        if r == "ended": return WIN
        if r == "timeout":
            if boss_defeated(io): return WIN
            log WARNING; turn += 1; continue        # never fake victory
        if use_tera and tera_available(): try_tera(tera_button)   # idempotent, retry each turn
        select_move(drain_punch_slot)
        turn += 1
    # Safety cap hit while still in battle
    log WARNING "boss not confirmed dead after {combat_max_turns} turns"; return UNCONFIRMED
```

`wait_for_battle_start()` gates end-detection: it waits for the first command/move menu or
`io.battle_state() is True` before the loop trusts `boss_defeated()` — otherwise the pre-battle
lobby (minimap in corner, host not yet IN_BATTLE) would read as "ended".

`select_move(slot)`: `A` (Fight) → `A` (open move list) → `2`×(slot−1) (down to slot) → `A`
(select) → `A` (confirm target; boss is auto-targeted, one confirm). Where possible each step is
gated on `is_move_select()` before advancing; delays from `move_select_delay_sec`. **Confirm the
real confirm-count live** — it may differ.

Stop authority: `io.battle_state()` (host FSM `IN_BATTLE` via the `/ws` push, `HostLink.in_battle`)
is authoritative when known; on `None` (WS down), vision `battle_ended()` decides. `A` is only ever
pressed on a detected text box, never blindly, so it can never mis-select a move.

Tera button token: `tera_button`, default `"R"` (valid `controller.click` token = R-trigger; host
uses the same). Configurable in case live testing shows a different mapping.

## 5. Coordinates & calibration (console currently offline)

All crop coordinates are expressed in **4K (3840×2160)** and auto-scaled by the capture lib
(`_sx`/`_sy`); templates match multi-scale (`scales=(1, 1.3, 2)`), exactly like the existing
detectors. Because the Switch 2 is offline, every combat detector ships with **placeholder** crop
rects / thresholds / PNG names inside a single clearly-labelled block per detector:

```
# ═══ CALIBRATE LIVE — replace crop rect, threshold, and reference PNG on real hardware ═══
```

A module-level flag guards them:

```
_BATTLE_DETECTORS_CALIBRATED = False   # flip to True after capturing the 5 reference PNGs
```

While `False`: the detectors return conservative values and `get_mode()`'s `IN_BATTLE` branch stays
inert — an uncalibrated build cannot false-positive and break `enter_tera_raid_lobby`. The macro is
**fully vision-gated**: it does not attempt combat until the detectors are calibrated (per user
decision — "prepare everything; the macro doesn't act until vision detection exists"). There is
**no** sleep-based combat fallback.

Reference PNGs to capture on the live feed (5): command menu ("Fight" button / menu frame),
move-select list, Tera orb lit, raid result/reward screen, and (for `battle_lost`) the defeat
screen. Capture them with the existing capture-window crop-save workflow (right-click-drag to
select → `f` → `screenshots/crop_*.png`), move them into `reference_images/`, set the crop rects to
the selection coordinates printed by the capture window, and flip the flag.

Deliverable: `tera-raid-farmer/CALIBRATION.md` — the step-by-step checklist for the above.

## 6. Config (`tera-raid-farmer/config.json`)

Add:

```jsonc
{
  "belly_drum_slot": 1,
  "drain_punch_slot": 2,
  "use_tera": true,
  "tera_button": "R",
  "combat_max_turns": 50,
  "turn_poll_timeout_sec": 20
  // keep: host_api_url, rewards_mash_count, auto_catch,
  //       move_select_delay_sec, com_port, baudrate
}
```

Deprecate/remove `screech_move_slot`, `battle_wait_sec`, and `poll_interval_sec` (Screech moved to
Switch 1; the fixed battle wait is replaced by the reactive loop; lobby polling is replaced by the
`/ws` push). The attacker's own between-frame vision cadence is a separate key,
`vision_poll_interval_sec` (default 0.3). `load_config()`'s defaults updated to match.

## 7. Testing

- **Offline (now):** unit-test the pure decision logic — the turn state machine
  (`wait_for_my_turn` transitions), the `HostLink.in_battle` mapping (fsm_state → true/false/none)
  and new-lobby edge detection,
  and the macro sequencing (Belly Drum once, then Drain Punch loop, Tera only ≥ turn 2, safety-cap
  WARNING) — by faking the detector/host-flag return values. No real frames needed. Validate
  detector thresholds against saved crop PNGs when any exist.
- **Live (blocked until a real raid):** capture the 5 reference PNGs, tune thresholds and crop
  rects, verify the Fight-menu confirm-count and the Tera button token, and calibrate
  `move_select_delay_sec`. Everything left for live is marked in `CALIBRATION.md`.

## 8. Out of scope

- No changes to `auto-raid-switch1` (host).
- No RAM peek/poke on Switch 2 (no sys-botbase there).
- No Screech on the farmer.
- No fixed-sleep combat as a primary path.
- No host HTTP polling (removed entirely; see the updated §1 — lobby and battle-end both ride the
  `/ws` push). Outbound one-shot POSTs (`start_battle`, `caught`) are kept.
