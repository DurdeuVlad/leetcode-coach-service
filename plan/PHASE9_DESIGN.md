# Phase 9 Design — Inline UI + Credit Budget System

**Status:** Approved brainstorming output, ready for implementation planning.
**Date:** 2026-07-30

## Understanding Summary

- **What:** Redesign the Telegram UX from plain-text to full inline button-driven
  interface, and replace the rigid daily cycle with a credit/debit budget system.
- **Why:** Current messages are ugly (raw URLs, wall-of-text) and the rigid
  propose→pick→expire cycle doesn't match how the user works (flexible timing,
  do more on free days, skip on busy days).
- **Who:** Single user (the owner). No multi-user concerns.
- **Constraints:** Telegram Bot API (inline keyboards, callback_data ≤64 bytes),
  existing stack (FastAPI, Postgres, APScheduler, SQLModel), no new services.
- **Non-goals:** No multi-user, no web UI, no Celery/Redis, no new external services.

## Decision Log

### UX / Interactivity

| # | Decision | Alternatives | Rationale |
|---|----------|-------------|-----------|
| 1 | Full inline UI — buttons everywhere | Formatting only; buttons for picks only | User wants minimal typing; buttons cover all actions |
| 2 | Direct pick (no confirm) + Cancel button | Toggle+Confirm; Two-step menu | Speed for daily use; cancel covers mistakes |
| 3 | Propose message stays as reference after picks | Delete; static summary | User can still see full 5-list, /why unpicked problems |
| 4 | Per-problem buttons: Skip, Hint, Solution, Why | Code+Skip only; Code+Skip+Hint+Solution | Full action set; no Send Code button (text reply for code) |
| 5 | Coach feedback buttons: Next, Re-attempt, Why This Lesson | Next+Re-attempt only; none | Closes the loop fully; lesson context aids learning |
| 6 | Card-style propose format with difficulty badges | Compact; minimal | Spacious, readable, scannable daily |
| 7 | Problem slug as universal callback_data key | Pick index+date; pending_review ID | Self-describing, survives across days, consistent everywhere |
| 8 | Pick state in bot_state table (DB-backed) | In-memory; encoded in message | Survives restarts, admin-visible, consistent with existing pattern |
| 9 | Validate on tap for stale buttons | Edit on expiry; both | Simpler for single-user; toast handles it |

### Budget System

| # | Decision | Alternatives | Rationale |
|---|----------|-------------|-----------|
| 10 | Credit/debit ledger | Fixed queue; streak target | Pure balance, most flexible, no hard "due" concept |
| 11 | Daily tax -2, hard=+2, medium=+1, easy=+0.5 (solved) | Flat rate | Difficulty-weighted rewards real effort. **Provisional defaults — pending Phase 7 calibration with real runtime data (see `business-requirements.md` §8). Not fixed decisions.** |
| 12 | Full scale: skip=0, saw_solution=0.25, reviewed=0.5, solved=full | All zero; partial only | Rewards every level of engagement. **Provisional — pending Phase 7 calibration.** |
| 13 | Balance = cumulative credits - (days × 2) | — | Positive = ahead, negative = behind |
| 14 | Propose refills when open queue < threshold | Scheduled only; on-demand only | Auto-refill ensures queue never empties. **Threshold (3) is provisional — pending Phase 7 calibration.** |
| 15 | Nudge at 20:00 if balance < 0 | No nudge; nudge at different time | Gentle evening reminder, not aggressive. **Time (20:00) is provisional — pending Phase 7 calibration.** |
| 16 | Expiry at 22:00 with [Extend to Tomorrow] button | 05:05 next day; no expiry | User-controlled; problems stay in queue regardless |

## Assumptions

- Daily tax accrues at 00:00 Europe/Bucharest (clean day boundary).
- Queue refill threshold: propose when <3 open (uncoached) problems in queue.
  **All numeric values in this section are provisional defaults pending Phase 7
  calibration (see `business-requirements.md` §8). They are starting points for
  implementation, not final tuning decisions.**
- The 09:05 cron becomes a "morning check" — if queue is low, propose; if not, skip.
- Existing `/propose`, `/pick`, `/coach` text commands remain as fallbacks.
- `leetcode_log` gets a `credits_earned` column (nullable float, backfilled as 0
  for historical rows).
- A new `credit_ledger` table tracks daily tax accruals and per-problem credit
  awards, so the balance is auditable and re-computable.
- `bot_state` stores: `pick_in_progress` (JSON), `propose_message_id`,
  `last_tax_date` (to handle restarts that miss a midnight tick).

## Architecture

### New Tables

```
credit_ledger:
  id          SERIAL PK
  date        DATE NOT NULL INDEX
  entry_type  VARCHAR(20)  -- 'tax' | 'solve' | 'review' | 'saw_solution' | 'skip' | 'adjustment'
  problem_slug VARCHAR(200) NULL  -- NULL for tax entries
  credits     FLOAT NOT NULL  -- negative for tax, positive for solves
  note        VARCHAR(500) NULL
  created_at  TIMESTAMPTZ DEFAULT now()
```

### Modified Tables

```
leetcode_log:
  + credits_earned FLOAT DEFAULT 0  -- denormalized for quick /status display

pending_review:
  + status values: 'open' | 'done' | 'expired' | 'skipped' | 'saw_solution'
    (currently: 'open' | 'done' | 'expired' — add 'skipped', 'saw_solution')
```

### New Scheduler Jobs

| Job | Schedule | Purpose |
|-----|----------|---------|
| `daily_tax` | `0 0 * * *` (midnight) | Accrue -2 credits to ledger |
| `evening_nudge` | `0 20 * * *` (20:00) | If balance < 0, send nudge with buttons |
| `queue_refill_check` | `5 9 * * *` (09:05, replaces old propose) | If open queue < 3, run propose_5 |

### Modified Jobs

| Job | Change |
|-----|--------|
| `expiry_sweep` | Move from 05:05 to 22:00. Don't delete problems — just mark propose buttons inert. Problems stay in queue. |

### Callback Handler Architecture

New module: `src/leetcode_coach/webhooks/callbacks.py`

```
callback_query handler
  → parse callback_data: "action:slug" or "action" (for non-slug actions)
  → if slug is an 8-char hex hash, resolve via bot_state callback_hash mapping
  → dispatch to action handler:
      pick:<slug>      → pick flow (first pick → store state, edit message; second pick → Flow B)
      cancel           → reset pick state, restore propose buttons
      skip:<slug>      → mark skipped, log 0 credits, close thread
      hint:<slug>      → reply with coaching_hint (toast or message)
      solution:<slug>  → mark saw_solution, log 0.25 credits, close thread
      why:<slug>       → run /why flow inline, reply with explanation
      next:<slug>      → find next open problem, send its thread message
      reattempt:<slug> → re-open the thread for another code submission
      why_lesson:<slug>→ explain the lesson saved/reinforced for this problem
      extend           → extend propose buttons to tomorrow
      snooze           → dismiss nudge until tomorrow morning
```

**callback_data ≤64 bytes mitigation:** `callback_data` is capped at 64
bytes by Telegram. `action:slug` where action is up to 11 chars
(`why_lesson`) + `:` + slug. LeetCode slugs can be up to 200 chars (per
the `LeetCodeProblem.slug` model), so `why_lesson:` (11 chars) + 200-char
slug = 211 bytes — far over the limit. The `encode_callback_data(action, slug)`
helper (built in #043) handles this: if `len(action) + 1 + len(slug) <=
64`, it returns `f"{action}:{slug}"` directly; otherwise it computes
`md5(slug)[:8]`, stores a `callback_hash:{hash} → slug` mapping in
`bot_state` (7-day TTL), and returns `f"{action}:{hash}"`. The handler
resolves the hash back to the slug on tap. Most LeetCode slugs are <55
chars so the hash path is an edge case, but it must be handled or the
Telegram `send_message` call will fail with a 400 error.

### Message Format Examples

#### Propose Message (card-style, HTML)

```html
<b>📊 Today's Problems</b>

<b>1. <a href="https://leetcode.com/problems/two-sum/">Two Sum</a></b> 🟢 easy
<i>array, hash-map</i>
<blockquote><b>Why:</b> warmup; targets your 'check empty input' lesson.
<b>Hint:</b> before writing code, ask: can I trade space for time?</blockquote>

<b>2. <a href="...">Binary Search</a></b> 🔴 hard
<i>array, binary-search</i>
<blockquote><b>Why:</b> reinforces your 'off-by-one on inclusive bounds' lesson.
<b>Hint:</b> pick your bounds convention and stick with it.</blockquote>

[... 3, 4, 5 ...]

<b>Credits: +3.5 (ahead 1 day)</b>
```

Inline keyboard:
```
[1] [2] [3] [4] [5]
```

After first pick (slug "two-sum"):
```
✅ Pick 1/2: Two Sum
Tap your second choice.

[2] [3] [4] [5]
[Cancel]
```

After second pick:
```
✅ Picks: Two Sum, Binary Search
Check the threads below 👇
```
(buttons removed)

#### Per-Problem Thread (HTML)

```html
<b>Problem 1/2: <a href="https://leetcode.com/problems/two-sum/">Two Sum</a></b> 🟢 easy

<blockquote>{coaching_hint}</blockquote>

Reply to this message with your code.
```

Inline keyboard:
```
[⏭️ Skip] [💡 Hint] [📖 Solution] [🤔 Why]
```

#### Coach Feedback (HTML)

```html
{tutor_feedback — 5 sections as-is}

<i>Saved lesson: <b>Check Empty Input</b>.</i>
```

Inline keyboard:
```
[▶️ Next Problem] [🔄 Re-attempt] [🤔 Why This Lesson?]
```

#### Pinned Message (HTML)

```html
📊 <b>Progress</b>
Credits: <b>+3.5</b> (ahead 1 day)
Open: 2 | Coached today: 1
📚 Active lessons: 3
🔥 Streak: 12 days
```

#### Nudge Message (HTML, sent at 20:00 if balance < 0)

```html
⚠️ You're behind by 2 credits.
Solve 1 hard or 2 mediums to catch up.
```

Inline keyboard:
```
[💪 Solve Now] [⏭️ Accept Deficit] [😴 Snooze to Tomorrow]
```

## Issue Breakdown

### #040 — Credit ledger table + migration
- New `credit_ledger` table
- Add `credits_earned` column to `leetcode_log`
- Add `skipped` / `saw_solution` to `pending_review.status` values
- Alembic migration
- Seed: backfill historical `leetcode_log` rows with credits based on status+difficulty

### #041 — Credit calculation + balance query
- Function: `compute_credits(status, difficulty) -> float`
- Function: `get_balance() -> float` (sum of ledger entries)
- Function: `accrue_daily_tax()` — insert -2 tax entry if not already accrued today
- Function: `award_credits(slug, status, difficulty)` — insert ledger entry + update leetcode_log.credits_earned
- Tests: credit values, balance computation, idempotent tax accrual

### #042 — Daily tax + nudge scheduler jobs
- `daily_tax` job at 00:00 — calls `accrue_daily_tax()`
- `evening_nudge` job at 20:00 — if balance < 0, send nudge message with buttons
- `queue_refill_check` job at 09:05 — replaces old `flow_a_propose_5`; only proposes if open queue < 3
- Move `expiry_sweep` from 05:05 to 22:00
- Tests: job registration, nudge conditional on balance

### #043 — Callback query handler infrastructure
- New `webhooks/callbacks.py` — parse `callback_data`, dispatch to handlers
- Register callback_query handler in main.py webhook route
- `answer_callback_query` wrapper in telegram.py (for toast notifications)
- `edit_message_reply_markup` wrapper in telegram.py (to remove/swap buttons)
- Tests: callback parsing, dispatch, unknown callback handling

### #044 — Propose message: card-style format + pick buttons
- Rewrite propose message builder to card-style HTML with badges, hyperlinks, blockquotes
- Add inline keyboard `[1] [2] [3] [4] [5]` with `pick:<slug>` callbacks
- Store `propose_message_id` in bot_state
- Pick flow: first tap → edit message + store `pick_in_progress` in bot_state; second tap → trigger Flow B, edit message to summary, remove buttons
- Cancel button: reset pick state, restore original buttons
- Stale button validation: if slug not in today's candidates, toast "expired"
- Tests: message format, pick flow state transitions, cancel, stale button

### #045 — Per-problem thread: action buttons
- Add inline keyboard `[Skip] [Hint] [Solution] [Why]` to per-problem message
- Skip handler: mark `pending_review.status = 'skipped'`, log 0 credits, edit message to remove buttons, refresh pinned
- Hint handler: reply with coaching_hint as a toast or follow-up message
- Solution handler: mark `pending_review.status = 'saw_solution'`, log 0.25 credits, edit message, refresh pinned
- Why handler: run /why flow inline (reuse existing logic), reply with explanation
- Tests: each button action, credit awarding, message editing

### #046 — Coach feedback: follow-up buttons
- Add inline keyboard `[Next] [Re-attempt] [Why This Lesson]` to coach reply
- Next handler: find next open pending_review, send its thread message (or "all done" toast)
- Re-attempt handler: reset `pending_review.status = 'open'`, re-send thread message
- Why This Lesson handler: LLM call explaining the lesson saved/reinforced, reply
- Tests: each button action, "all done" case, re-attempt re-opens thread

### #047 — Nudge message + buttons
- Nudge message builder (HTML, shows deficit amount)
- Inline keyboard: `[Solve Now] [Accept Deficit] [Snooze]`
- Solve Now: list open problems with pick buttons
- Accept Deficit: dismiss nudge, log "accepted_deficit" note
- Snooze: store `nudge_snoozed_until` in bot_state, suppress nudges until that date
- Tests: nudge sent when balance < 0, not sent when balance ≥ 0, button actions

### #048 — Pinned message: add credits display
- Add credits balance line to pinned snapshot
- Format: `Credits: +3.5 (ahead 1 day)` or `Credits: -1.0 (behind 0.5 days)`
- Refresh after: tax accrual, solve, skip, saw_solution, nudge dismiss
- Tests: balance display, refresh triggers

### #049 — Expiry redesign: 22:00 + extend button
- Move expiry sweep to 22:00
- On expiry: edit propose message to remove buttons, append "⏰ Buttons expired — problems still in queue"
- Add `[Extend to Tomorrow]` button on expired propose message
- Extend handler: re-add pick buttons, store `propose_extended_until` in bot_state
- Problems stay in queue (pending_review stays 'open' unless explicitly skipped)
- Tests: expiry edits message, extend re-adds buttons, problems persist

## Implementation Order

```
#040 (schema) → #041 (credit logic) → #042 (scheduler)
                                          ↓
#043 (callback infra) → #044 (propose UI) → #045 (thread buttons)
                                              ↓
                                         #046 (coach buttons)
                                              ↓
                                         #047 (nudge) → #048 (pinned) → #049 (expiry)
```

#040-#042 are the budget foundation (no UI).
#043 is the callback infrastructure (enables all UI issues).
#044-#046 are the core inline UI.
#047-#049 are the polish layer.
