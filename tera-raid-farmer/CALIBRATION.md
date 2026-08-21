# Phase-2 calibration — Switch 2 Iron Hands attacker vision

Status after the 2026-08-18 live session (real SV Tera Raid, 1080p Elgato feed).

## Tooling

`python tera-raid-farmer/calibrate.py` — opens the capture feed + Xbox passthrough
(both optional/robust), writes the current frame to `calib_frames/live.png` every
~0.7 s, and archives each *distinct* screen to `calib_frames/<time>.png`
(deduplicated). Everything is logged to `calib_frames/calibrate.log`. Drive the
Switch by hand; the assistant reads the frames to calibrate.

## DONE — vision detectors (validated against real frames)

`utils/pokemon_sv.py`, `_BATTLE_DETECTORS_CALIBRATED = True`. Templates in
`reference_images/`. All coords are 4K (auto-scaled); templates cut at 1080p and
the feed is 1080p, so `scales=(1,)`.

| Detector | Template | 4K region | Threshold | Validation |
|---|---|---|---|---|
| `is_command_menu` | `raid_command_battle.png` | 2800,1480,3840,1860 | 0.85 | POS 0.98–1.00, NEG 0.00 ✅ |
| `is_move_select` | `raid_move_info.png` | 1560,1950,2300,2120 | 0.90 | POS 0.996–1.0, NEG ≤0.74 ✅ |
| `tera_available` | `raid_tera_prompt.png` | 2160,1560,2800,2000 | 0.80 | POS 1.0 (one frame) ⚠️ |
| `get_move_slot` | — (gold `#d3b013` fill, tol 55) | 4 strips, see below | ≥0.50 | slot1 0.82, slots2-4 0.00; off-list 0.00 ✅ (slot1 only) |
| `battle_ended` (result) | `raid_result_rewards.png` | 200,400,840,600 | 0.85 | POS 1.0, NEG 0.00 ✅ |
| `battle_ended` (overworld) | — (minimap Hough) | — | — | already worked |

`get_move_slot` (added 2026-08-19, live raid): which move row (1-4) is highlighted, from
the gold selection fill in a clean strip per row — same technique as `get_portal_option`.
4K strips: slot1 `(3200,1230,3700,1370)`, slot2 `(3200,1454,3700,1594)`, slot3
`(3200,1678,3700,1818)`, slot4 `(3200,1890,3700,2030)`. Returns 0 off the move list, so
it doubles as a cursor-on-a-move signal. Only slot-1 positives were available live (the
raid ended early); **confirm slots 2-4 light up when highlighted** on the next pass.

Confirmed facts:
- Raid command menu is **Battle / Cheer / Run** (not Fight/Bag/Pokémon/Run). One
  **A** on "Battle" opens the move list.
- Move order for Iron Hands: **Belly Drum = 1**, **Drain Punch = 2**, Close Combat = 3,
  Heavy Slam = 4 (matches config).
- **Tera = R**, and the "TERASTALLIZE! [R]" prompt appears in the **move-select**
  screen — so the macro presses R after opening Battle (not in the command menu).
- Catch: after the boss falls, a **Catch / Don't Catch** prompt → then a **horizontal
  ball carousel** (selected ball centered, name shown above, ◄ ► to move, A to throw).
  OCR reads the centered name (e.g. "TimerBall"), so the macro navigates Right until
  it reads the target ball. Handled by `attacker.catch_pokemon`.

## KEY FINDING (live 2026-08-19): raid combat is REAL-TIME

The raid boss acts on its own cadence and its actions **dismiss the open command/move
menu at any moment**. A blind fixed-timing button sequence therefore desyncs: leftover
presses land on the wrong screen, and because the command menu defaults to **Battle**
and the move list to the **top slot (Belly Drum)**, stray A's re-cast Belly Drum. This
was the observed "sempre a dar Belly Drum" bug. Fix: **all combat navigation is now
vision-gated and self-correcting** (see the design doc
`docs/superpowers/specs/2026-08-19-raid-combat-vision-gated-navigation-design.md` and the
rewritten `attacker.take_turn` / `open_move_list` / `goto_move_slot` / `commit_move`).

Also confirmed live: **Switch 2 stays OFFLINE** (join is local link-code — earlier notes
that claimed online was required were wrong); command menu = Battle/Cheer/Run, default
Battle, 1×A opens the move list; move order Belly Drum(1)/Drain Punch(2)/Close Combat(3)/
Heavy Slam(4); "TERASTALLIZE! [R]" shows in the move list; boss Grumpig used a
"nullified the stat changes" action (raid bosses periodically clear buffs).

## STILL NEEDS A LIVE PASS

1. **Move-list navigation (`attacker.take_turn`).** ✅ REDESIGNED 2026-08-19 to be
   vision-gated (`get_move_slot` + `goto_move_slot`, delta-stepping so wrap no longer
   matters; commit verified by leaving the list, not by counted A's). Covered by unit
   tests incl. boss-interrupt recovery. **Live: confirm `get_move_slot` for slots 2-4
   and the end-to-end state machine.**
2. **Tera (`tera_available`).** Prompt confirmed visible in the move list. Still confirm
   the 0.80 threshold across frames and that pressing **R** actually Terastallizes.
3. **Catch flow (`attacker.catch_pokemon`).** Unchanged (already gated on
   `at_ball_select`). Verify live: the A accepting "Catch" doesn't overshoot into an
   early throw; Right (`"1"`) moves the carousel; OCR lands on Timer Ball; nickname
   prompt dismissed by the reward tail.
4. **`battle_lost` / win-vs-loss disambiguation.** MECHANISM CONFIRMED (user, 2026-08-19):
   **on a loss the game returns to the overworld ON ITS OWN — no catch prompt, no rewards.**
   The host FSM has no defeat state (states: IDLE/CONNECTING/SEARCH_LOOP/EVALUATING/
   SNAPSHOT_RAM/HOSTING_LOBBY/WAITING_FOR_FARMER/IN_BATTLE/BATTLE_REWARDS/IN_MEMORY_RESET/
   FARM_RENDER/PAUSED/STOPPED), so loss is a Switch-2-side vision fact, not a host signal.
   Implication (a real bug today): when the host leaves IN_BATTLE on a loss, `boss_defeated`
   returns True → macro reports a **false win** → `main.py` runs the catch and calls
   `signal_caught` for a mon that was never caught. So win/loss must be disambiguated by
   the post-battle screen: **WIN → catch prompt / ball carousel / rewards appear; LOSS →
   straight to the overworld (minimap) with none of those.** NOT YET IMPLEMENTED — a single
   frame can't tell win-overworld (after rewards) from loss-overworld, so it needs a
   *sequence* detector, which needs a live LOSS captured (and the win catch-prompt frame)
   to build safely. Implementing it blind risks a false loss that skips a real catch. Kept
   inert for now (safe). **Live TODO: lose one raid on purpose to capture the loss tail.**
5. **Link-code entry keypad (`enter_tera_raid_lobby`).** ✅ DONE 2026-08-19 (live, and
   re-confirmed this session joining code 2303 with the Switch 2 offline). See "Lobby
   entry (item 5)" below.

## Lobby entry — DONE (item 5, live 2026-08-19)

Full path verified live on the real Switch 2 (offline feed, bogus code "8093"):
`Main Menu → Poké Portal → Tera Raid Battle → Link Code keypad → type → search`.

- **Both menu hops are now vision-driven / dynamic** (detect the highlighted option,
  move by the delta), so cursor start position no longer matters:
  - Main Menu → Poké Portal: existing `pokemon_sv.goto_menu_option(MenuOption.PORTAL)`.
  - Poké Portal → Tera Raid Battle: NEW `pokemon_sv.goto_portal_option(PortalOption.TERA_RAID)`
    + `get_portal_option()`. Detects the gold (`#ffe300`, tol 80) selection fill in a
    clean strip per option (label-bar right half for the two image tiles; area right
    of the text for the five list rows). Validated: own ratio ~1.0, every other
    option ~0.0. Regions (4K) in `_PORTAL_OPTION_REGIONS`. Order top→bottom:
    Union Circle, Tera Raid Battle, Link Trade, Surprise Trade, Link Battle,
    Battle Stadium, Mystery Gift. List steps by exact delta (no wrap relied on).
- **Search screen:** the "Link Code" card is pre-selected; **A** opens the keypad.
- **Numeric keypad** (`main.py:type_link_code_numeric`, replaces the QWERTY typer):
  ```
    1 2 3   ⌫(B)      Up  clamps at the top row
    4 5 6   ┌────┐    Down clamps at the bottom → the '0' key (from any column)
    7 8 9   │ OK │    Left/Right WRAP horizontally
      0     └────┘    A enters the highlighted digit (cursor stays); opens on '1'
  ```
  - `+` presses **OK directly** (no need to navigate to it) **and fires the search**
    — so `enter_tera_raid_lobby` must NOT press an extra A to search afterwards.
  - `B` = backspace directly; `X` = cancel the keypad.
  - Typer tracks (row, col) from the known start and moves by exact deltas, staying
    in cols 0-2 / rows 0-2 for digits (never presses past an edge, so the horizontal
    wrap never triggers), and special-cases the lone '0' (Down-to-bottom to reach it,
    Up back to '8' to leave it). Verified typing "8093" (exercises 0 in/out + corner
    hops) → exactly "8093".
- Offline, `+` returns a "No raid was found" text box (expected). The online
  join/lobby/"Ready!" tail still needs a live raid to confirm (folded into items 1-3).

## Host stop-signal (already wired, no calibration, no polling)

The macro stops attacking on the host's FSM state via the `/ws` push (`host_link.py`):
`fsm_state == "IN_BATTLE"` while the raid runs, `BATTLE_REWARDS`/etc. once the boss is
down; vision `battle_ended()` is the fallback. `config.json` `host_api_url` must point
at the Switch 1 host (converted to `ws://…/ws`).
