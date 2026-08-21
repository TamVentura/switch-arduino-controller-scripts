# Design — Boss-interrupt-resilient, vision-gated raid combat navigation

Date: 2026-08-19
Scope: `tera-raid-farmer/attacker.py` (combat state machine) + `utils/pokemon_sv.py`
(new move-highlight detector). Supersedes the blind-timing `take_turn`.

## Problem

Tera Raid combat in SV is **real-time**, not turn-locked. The raid boss acts on its
own cadence and its actions **dismiss the player's open command/move menu** at any
moment. The previous `take_turn` was a blind fixed sequence

    A (Battle) → [R] → Up×3 → Down×(slot-1) → A (select) → A (confirm)

paced only by `move_select_delay_sec`. When the boss interrupts mid-sequence the menu
closes and the *remaining* presses land on whatever screen is now showing. Because the
command menu's default cursor is **Battle**, and the move list's default is the **top
slot (Belly Drum)**, stray `A` presses re-open Battle and re-cast **Belly Drum**. This
is the observed "sempre a dar Belly Drum" failure (live, 2026-08-19).

Root cause: **open-loop button navigation in a real-time environment.** Any fixed
sequence eventually desyncs; the fix is to gate every press on vision.

## Principle

Mirror the menu navigation already proven for the Poké Portal
(`pokemon_sv.goto_portal_option` / `get_portal_option`): **read the current on-screen
state, act by the exact delta, then re-verify.** If the expected screen is not present
(the boss closed it), **recover and retry, bounded.** Never press a committing button
unless vision confirms the exact expected screen.

## New vision primitive — `pokemon_sv.get_move_slot()`

Which of the 4 move rows is highlighted, from the gold selection fill (exactly the
technique `get_portal_option` uses for the portal's `#ffe300` fill).

- Highlight colour: **`#d3b013`**, per-channel tolerance **55** (measured live).
- Clean strips (4K), right side of each move card, clear of the icon/name:
  - slot 1: `(3200, 1230, 3700, 1370)`
  - slot 2: `(3200, 1454, 3700, 1594)`
  - slot 3: `(3200, 1678, 3700, 1818)`
  - slot 4: `(3200, 1890, 3700, 2030)`
- `get_move_slot(frame=None) -> int`: the slot (1-4) whose fill ratio is highest and
  ≥ **0.5**, else **0**.
- Calibrated from live frames (2026-08-19): on a slot-1-highlighted frame slot 1 ≈ 0.82
  and slots 2-4 = 0.00; on non-move-list frames all ≈ 0.00. Only slot-1 positives were
  available live, but the 4 rows are structurally identical at a fixed ~110px (4K 220px)
  pitch, so a highlight in any row fills its strip the same way. **Confirm slots 2-4
  live** on the next pass.
- Because it returns 0 off the move list, it doubles as a "cursor is on a move" signal,
  complementing `is_move_select()` (which anchors on the "Y Move Info" prompt).

## New navigation helpers — `attacker.py`

New `BattleIO` seam: `get_move_slot: Callable[[], int]` (wired to
`pokemon_sv.get_move_slot` in `real_io`; defaults to `lambda: 0` for tests/other flows).

- **`open_move_list(cfg, io) -> bool`** — ensure the move list is open, bounded by
  `menu_action_timeout_sec`:
  - already `is_move_select()` → True;
  - else `is_command_menu()` → press **A** once (Battle), then re-verify `is_move_select()`;
  - else `at_text_box()` → press **A** to advance;
  - else wait `vision_poll_interval_sec`.
  Returns True only when `is_move_select()` is confirmed. It presses **A on the command
  menu at most once per attempt**, so it can never walk into a committed move.

- **`goto_move_slot(target, cfg, io) -> bool`** — bounded by `move_nav_max_steps`:
  read `get_move_slot()`; if 0 (list gone / boss interrupt) → return **False** (caller
  re-opens); if == target → True; else step **Down("2")/Up("4")** by the exact delta,
  settle `move_select_delay_sec`, re-read. Delta-stepping is correct whether the list
  clamps or wraps at the ends (same argument as `goto_portal_option`), so the previously
  open "does the move list wrap?" question no longer matters.

- **`commit_move(cfg, io) -> bool`** — press **A** (raid boss is auto-targeted, single
  target); verify we **left** the move list (`is_move_select()` False) within
  `menu_action_timeout_sec` → the action locked in. If still in the move list, the press
  didn't take (boss interrupt) → return False.

## Rewritten `take_turn(slot, want_tera, cfg, io) -> bool`

A bounded, self-correcting state machine:

```
deadline = now + menu_action_timeout_sec * 4       # whole-turn budget
while now < deadline:
    if boss_defeated(io):            return True    # nothing left to do
    if not open_move_list(cfg, io):  continue       # boss acting; reassess
    if want_tera and use_tera and tera_available(io):
        click(tera_button)                          # R; stays in move list; idempotent
    if not goto_move_slot(slot, cfg, io): continue  # list dismissed mid-nav; reassess
    if commit_move(cfg, io):         return True     # locked in
    # else: boss interrupted the commit — loop and reassess
return False                                        # couldn't act; caller retries next loop
```

Key safety property: **`A` is only ever pressed to (a) open Battle when the command menu
is confirmed, (b) advance a confirmed text box, or (c) commit when confirmed on the
target slot.** A boss interrupt at any point drops control back to re-assessment; it can
never re-cast Belly Drum, because committing requires vision-confirmed presence on the
target slot. Tera is best-effort (a missed R just retries next turn); pressing R inside
the move list is safe and idempotent.

`take_turn` now returns `bool` (acted / couldn't-act). The outer macro
(`run_attacker_macro`) already re-checks `boss_defeated` / `battle_lost` and re-enters
`wait_for_my_turn` each loop, so a `False` return simply means "no committed action this
pass — try again", which the loop already tolerates. `wait_for_my_turn` keeps advancing
text boxes and waiting for the next command menu (unchanged).

## Config knobs (AttackerConfig / config.json)

- `menu_action_timeout_sec: float = 4.0` — per menu-step bound (open list / commit).
- `move_nav_max_steps: int = 6` — cursor steps before giving up a navigation attempt.
Existing `move_select_delay_sec` remains the per-press settle; existing
`vision_poll_interval_sec` the idle poll.

## Out of scope / still needs live confirmation

- **Item 3 (catch):** `catch_pokemon` is already vision-gated on `at_ball_select()`
  (OCR ball name) and stops the instant the carousel is detected. Unchanged here;
  still needs the live catch/OCR/nickname-dismissal confirmation.
- **Item 4 (`battle_lost` / win-vs-loss):** mechanism confirmed (user, 2026-08-19) — on a
  loss the game returns to the overworld on its own, with **no catch prompt and no
  rewards**; the host FSM has no defeat state, so loss is a Switch-2 vision fact. This
  means the current "host left IN_BATTLE ⇒ win" is wrong on a loss (it would trigger a
  false catch + `signal_caught`). Correct design: disambiguate by the post-battle screen
  (WIN → catch prompt / carousel / rewards; LOSS → straight to overworld). Needs a
  *sequence* detector built from a live LOSS capture (a single overworld frame can't tell
  win-after-rewards from loss); NOT implemented here to avoid a false-loss that skips a
  real catch. Detector stays inert (safe) until the live loss tail is captured.
- **Live confirmation of the whole state machine and of `get_move_slot` for slots 2-4**
  is the remaining live pass. This design removes the structural desync bug offline and
  is covered by unit tests with simulated boss interrupts.

## Testing

Unit tests (fake seams, no hardware) cover:
- `get_move_slot` seam plumbing.
- `open_move_list`: from move-list / command-menu / text-box / nothing; bounded timeout.
- `goto_move_slot`: correct delta up/down; returns False when the list vanishes mid-nav;
  bounded by `move_nav_max_steps`.
- `commit_move`: True when it leaves the list, False when it doesn't.
- `take_turn`: happy path per slot; **boss-interrupt recovery** (list dismissed during
  nav / commit → retries and still lands on the right slot, never on Belly Drum);
  returns False when it can never open the list; Tera pressed only when available & on
  turn 2+.
- Existing macro-sequencing tests keep passing (take_turn is monkeypatched there).
