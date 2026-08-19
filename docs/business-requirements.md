# Business Requirements — LeetCode Coach

> **Status: V1 historical record — superseded 2026-08-03.**
>
> This document records the original port contract. The canonical behavioral
> contract is now [`agentic-v2.md`](./agentic-v2.md); its requirements override
> every conflicting statement below, including V1's prohibition on an LLM
> tool-calling loop, Gemini fallback, and four-table schema. Keep this material
> for migration provenance and behavior comparison only. Do not implement new
> work from it.

## 1. Purpose

A personal, always-on coaching system that drives a daily LeetCode practice toward
Google-tier and top-fintech interview readiness. Replaces the earlier Discord-based
version. The system is single-user (Vlad) and runs on a homelab. It is **not** a
multi-tenant product; authn/authz is limited to "only Vlad's Telegram chat can drive
the bot."

## 2. User model

- One user. One Telegram chat. One LeetCode account.
- Daily cadence: morning problem proposal, day-time attempt, evening expiry sweep.
- Active ~10-12 hrs/week alongside a full-time job; the system must respect that
  load by capping daily volume (see §4).

## 3. External surfaces

| Surface | Direction | Role |
|---|---|---|
| Telegram bot | bidirectional | primary UI: receives picks + code replies, sends proposals + feedback |
| LLM provider (OpenAI primary, Gemini fallback) | outbound | candidate selection + coach pass |
| LeetCode GraphQL via Browserless (homelab) | outbound (weekly) | refreshes the unsolved problem pool; headless Chrome gets past Cloudflare |
| YouTube search via SearXNG (homelab) | outbound (per coach pass) | finds tutorial videos for the coach prompt |
| Homelab Postgres | internal | all persistent state |

## 4. Functional requirements

### FR-1 — Daily candidate proposal (Flow A)

> **Superseded by Agentic V2 (2026-08-16):** FR-1.1 through FR-1.4 and
> FR-1.7 no longer constrain current behavior. Proposals contain one or more
> coach-chosen problems; there is no exact-five, difficulty-mix, eligibility,
> solved-state, or pre-populated-pool gate. Exact slug/URL normalization and
> deterministic paginated Telegram rendering remain code-enforced. A mechanical
> maximum of 20 candidates keeps the controller keyboard Telegram-safe; it is not
> a pedagogical proposal-count rule.

- **FR-1.1** Once per day at 09:05 Europe/Bucharest, the system proposes **5 candidate
  problems** to the user via Telegram.
- **FR-1.2** The 5 candidates must be drawn from the **unsolved problem pool**
  (`leetcode_problems` where `solved = false`), allowing at most 1 already-solved
  problem for spaced repetition.
- **FR-1.3** Difficulty mix: 2-3 hard + 2-3 medium. Never 5 of one difficulty.
- **FR-1.4** Selection is biased by:
  1. Active `tutor_lessons` (weak patterns the user is reinforcing).
  2. Recent `leetcode_log` (last 30 rows) for difficulty calibration and repeat
     avoidance.
  3. Each candidate should target at least one active lesson where possible.
- **FR-1.5** Each candidate carries two personalized fields shown to the user:
  - `reasoning` (1-2 sentences, references the user's actual data — which weak
    pattern it targets, why the difficulty is appropriate).
  - `coaching_hint` (1 line, drawn from active lessons).
- **FR-1.6** The proposal is one Telegram message, numbered list, with reasoning
  visible per candidate. The flow then **ends** — it does not wait for the reply.
  Replies are handled by Flow B (FR-2). On-demand trigger via `/propose` (FR-6)
  also starts Flow A; the flow still ends after sending.

  **Rendering decision (2026-07-30, see `docs/telegram-formatting.md`):** the
  propose message is rendered in **code** from the `candidates` array, not by
  the LLM. The LLM emits only the `candidates` array with plain-text fields
  (`title`, `tags`, `reasoning`, `coaching_hint`, etc.); the code builds an
  HTML card (`<b>`, `<a href>`, `<blockquote>`, difficulty emoji 🔴🟡🟢) and
  sends it with `parse_mode="HTML"`. The previous design had the LLM emit a
  `candidate_list_markdown` field (MarkdownV2), but the code sent it as plain
  text (no `parse_mode`) because MarkdownV2 rejects on missing escapes —
  producing visible `\.`/`\-` escape artifacts in the user-facing message.
  HTML + `html.escape` (3 escape characters) is strictly simpler than
  MarkdownV2 (19 escape characters) and the LLM never touches the escaping.
- **FR-1.7** The LLM must never invent problem titles or URLs. If it cannot confirm
  a problem exists from the pool, it must skip it.

### FR-2 — Reply routing and coach pass (Flow B)

- **FR-2.1** A single Telegram webhook receives all incoming messages. Routing is
  data-driven (reply-to-message correlation per FR-2.2), with one text-driven
  exception: slash commands (FR-6) are parsed before data-driven routing.
- **FR-2.2** Correlation priority:
  1. If the incoming message has `reply_to_message.message_id`, look up
     `pending_review` by that exact `message_id`. Found → coach pass path.
     Not found → pick-parse path (the reply was to the 5-list message, whose ID
     is never stored in `pending_review`).
  2. If no `reply_to_message`: fuzzy-match the text against today's open
     `pending_review` rows by problem title. Exactly one match → coach pass.
     Zero or multiple matches → send a clarification prompt
     ("Which one — 1) X 2) Y?") and stop. **Never guess.**
- **FR-2.3** Pick-parse path: parse the reply as ≤2 numbers (regex, no LLM),
  map to the 5-candidate list, cap at 2 chosen problems. Empty/invalid →
  short "no valid picks" message, log nothing.

  **Superseded by Agentic V2 (2026-08-16):** the current callback flow toggles
  any number of candidate selections and commits them with **Done**. The
  historical two-pick cap does not apply.
- **FR-2.4** For each chosen problem, in order:
  1. Send an individual Telegram message ("Problem 1/2: ...") including the
     `coaching_hint`. Capture its `message_id`.
  2. Insert a `pending_review` row: `message_id`,
     `problem_slug`, `problem_title`, `proposed_at = today`, `status = open`.
     (The Google Task creation step was removed in v1 — see §8 decision 5.)
- **FR-2.5** Coach pass: an LLM call that reads the user's submission text plus
  the problem metadata plus the user's active `tutor_lessons`. Output:
  - If code was pasted: **coaching**, not just grading —
    - Correctness (does it work? what edge case breaks it? honest, no false praise)
    - Complexity (Big-O time/space, whether optimal for this pattern)
    - Style/idiom (language-specific)
    - Pattern coaching (what category this problem exercises, how it connects
      to active lessons, what the next-level version looks like)
    - Next step (one concrete recommendation)
  - If a status note was pasted ("skipped", "saw solution"): log status only,
    no review. If "saw solution," add one line on the key takeaway.
- **FR-2.6** Lesson decision (the adaptability loop):
  - The coach decides whether a **generalizable** lesson surfaced. Generalizable
    = a pattern that applies to multiple problems, not a one-off bug or typo.
  - If an existing active lesson matches (by title similarity or same category +
    same pattern): bump `times_reinforced` on the existing row. Do not duplicate.
  - If a new lesson surfaces: insert a new `tutor_lessons` row with
    `times_reinforced = 1`, `active = true`.
  - Graduation is **double-gated**: coach says `lesson_should_graduate = true`
    **AND** the existing row's `times_reinforced >= 5` (read from DB, not from
    the coach). On graduation: set `active = false`. The coach feedback says
    "I'm retiring this lesson" explicitly.
- **FR-2.7** After the coach pass:
  1. Insert a `leetcode_log` row (full schema, including `lesson_title` if a
     lesson fired).
  2. If solved: mark `leetcode_problems.solved = true`.
  3. Update the `pending_review` row: `status = done`.
  4. Reply on Telegram with a short confirmation + coach feedback, explicitly
     naming any lesson saved, reinforced, or retired.

  (The Google Task "mark complete + append feedback to notes" step was
  removed in v1 — see §8 decision 5. The coach feedback is delivered via
  the Telegram reply in step 4 instead.)

### FR-3 — Expiry sweep

- **FR-3.1** Once per day at 05:05 Europe/Bucharest, sweep all `pending_review`
  rows for the current day where `status = open`.
- **FR-3.2** For each: set `status = expired`. (The Google Task notes-append
  step was removed in v1 — see §8 decision 5.)
- **FR-3.3** Send one Telegram summary message listing the expired problems
  (or "No problems expired today" if none).

### FR-4 — Weekly problem pool refresh

- **FR-4.1** Once per week, pull the user's LeetCode problem history via the
  LeetCode GraphQL API and upsert into `leetcode_problems`.
- **FR-4.2** All LeetCode GraphQL calls go through the homelab Browserless
  instance (headless Chrome). Cloudflare's 2026 bot detection blocks
  unauthenticated programmatic GraphQL from datacenter/homelab IPs; running
  the same `fetch()` from within a real Chrome page context is the primary
  path, not a fallback. If Browserless is unavailable, raise
  `LeetCodeFetchError` — do not attempt a direct httpx call.

### FR-5 — Adaptability loop (cross-flow)

- **FR-5.1** Flow B saves / reinforces / graduates lessons → Flow A reads
  active lessons next day → candidates target weak patterns → Flow B checks
  if the student demonstrated the lesson → repeat or graduate.
- **FR-5.2** This is the system getting smarter about the user over time, not
  just logging attempts. A regression here is a regression in the core value
  of the system.

### FR-6 — Slash commands (interactive control)

- **FR-6.1** The bot recognizes a fixed set of slash commands. If a message
  starts with `/`, it is routed as a command before FR-2.2 reply correlation
  runs.
- **FR-6.2** `/propose` — trigger Flow A immediately. Same effect as the 09:05
  cron. Cron still runs on schedule; commands are additive.
- **FR-6.3** `/pick <n1> [<n2>]` — trigger Flow B pick-parse path with the
  given 1-based indices. Same effect as replying "1 2" to the 5-list message.
- **FR-6.4** `/coach <text>` — trigger Flow B coach pass. If >1
  `pending_review` is open today, requires a target: `/coach <slug> <text>`
  or a reply-to. No target → short error, no LLM call.
- **FR-6.5** Commands only work from the allowlisted chat ID (NFR-4). No new
  auth surface.
- **FR-6.6** Unknown command → short "unknown command" message, no LLM call,
  no DB write.

### FR-7 — Progression queries (read-only)

- **FR-7.1** `/status` — reply with a structured text dump (no LLM call):
  active lessons (title + `times_reinforced`), last 7 days of `leetcode_log`
  (date, problem, solved?, lesson), current streak (consecutive days with
  ≥1 coached attempt). Cheap and deterministic.
- **FR-7.2** `/why <slug>` — one LLM call, 2-3 sentences, explaining why a
  problem was proposed or what lesson it targets. Bounded to a single call.
- **FR-7.3** Progression queries are read-only: they never insert or update
  any row.

### FR-8 — Pinned progression message

- **FR-8.1** The bot maintains one pinned message in the allowlisted chat with
  a compact snapshot: today's status counts (proposed/picked/coached/expired),
  active lessons count, current streak.
- **FR-8.2** Refresh trigger: updated after each Flow A run, Flow B pick, and
  Flow B coach pass — whenever the snapshot's inputs change. No new cron job.
- **FR-8.3** The pinned message ID is stored in a new `bot_state` key-value
  table (not an env var) so it can be updated without redeploying.
- **FR-8.4** If `editMessageText` fails (message deleted, permissions
  changed), the bot creates a new pinned message and stores the new ID.

## 5. Data model

Five tables. Column names are case-sensitive and referenced by name in code.

### `leetcode_problems`
| Column | Type | Notes |
|---|---|---|
| `title` | string | LeetCode problem title |
| `slug` | string | URL slug, e.g. `two-sum` (PK) |
| `url` | string | full URL |
| `difficulty` | string | `easy` / `medium` / `hard` |
| `tags` | string | comma-separated, e.g. `array,hash-map` |
| `solved` | boolean | default `false` |
| `last_attempted` | date | nullable |
| `times_attempted` | number | default `0` |

### `leetcode_log`
| Column | Type | Notes |
|---|---|---|
| `problem_slug` | string | FK to `leetcode_problems.slug` |
| `date` | date | when the attempt happened |
| `status` | string | `solved` / `reviewed` / `skipped` / `saw_solution` |
| `time_spent_min` | number | nullable |
| `tutor_feedback` | text | nullable; the coach's feedback |
| `lesson_title` | string | nullable; the lesson saved on this attempt, if any |

### `pending_review` — tracks up to 2 concurrent open problems per day
| Column | Type | Notes |
|---|---|---|
| `message_id` | number | Telegram message_id of the per-problem msg; correlation key |
| `problem_slug` | string | FK |
| `problem_title` | string | denormalized for fuzzy match |
| `proposed_at` | date | when Flow A sent the msg |
| `status` | string | `open` / `done` / `expired` |

### `tutor_lessons` — the memory system
| Column | Type | Notes |
|---|---|---|
| `title` | string | short, e.g. `check empty input before binary search` |
| `category` | string | e.g. `binary-search`, `dp`, `graphs` |
| `created_at` | date | first seen |
| `times_reinforced` | number | default `1`; bumped when the same pattern recurs |
| `active` | boolean | default `true`; set `false` when mastered |

### `bot_state` — key-value store for runtime state (FR-8)
| Column | Type | Notes |
|---|---|---|
| `key` | string | primary key, e.g. `pinned_message_id` |
| `value` | string | JSON-encoded value; consumer parses per key |
| `updated_at` | timestamptz | set on every write |

A single-row-per-key store for runtime state that must survive restarts but
should not require a redeploy to change. Currently used only for the pinned
progression message ID (FR-8.3). Add keys as new stateful features arrive;
do not add columns to existing tables for one-off state.

## 6. Non-functional requirements

### NFR-1 — Reliability
- The system runs daily. A missed day is a missed day of practice; reliability
  is the headline NFR.
- Three error layers (mirrored from the n8n v3 spec):
  1. **Retry** on transient failures (HTTP 429, timeouts, 5xx) for every
     external call: LLM, Telegram, LeetCode GraphQL. Max 2-3 tries with
     short backoff.
  2. **Typed error branches** for known non-recoverable failures:
     LeetCode-fetch and LLM-provider failures. Never let the LLM "log
     with estimated defaults" to paper over an infra failure.
  3. **Global catch** that sends one Telegram alert for anything that escapes
     layers 1 and 2.

### NFR-2 — Cost
- LLM cost ceiling: **<$10/month** at the assumed usage pattern (1 Flow A run
  + up to 3 Flow B runs per day). Verified pricing in `architecture.md`.
- Fallback model only fires when the primary actually fails; it is not counted
  in the steady-state cost.

### NFR-3 — Latency
- Flow A (proposal): user-facing, target <30s end-to-end.
- Flow B (coach pass): user-facing, target <60s (LLM call dominates).
- Expiry sweep: not user-facing, no target.

### NFR-4 — Security
- Telegram `chatId` allowlist: only Vlad's chat ID can drive the bot.
- Secrets (LLM API keys, Telegram bot token, YouTube API key if used) live
  in environment variables, never in the repo.

### NFR-5 — Operability
- Single Docker container deployable on Coolify.
- Healthcheck endpoint.
- Structured logs (JSON) to stdout, shipped by Coolify.
- One-command local run for development (`uv run uvicorn ...` or equivalent).

## 7. Out of scope (v1)

- Multi-user support.
- A web UI. Telegram is the only UI.
- Image / photo evidence of solutions (the v2 n8n spec deferred this; still
  deferred).
- Pushing lessons back to Anki or any other spaced-repetition system.
- Automated mock interviews (the Moonshot Plan covers those separately via
  Pramp / interviewing.io).
- Free-form conversational AI (multi-turn chat with tool use). v1 is one-shot
  LLM calls only. See `architecture.md` §12.

## 8. Open decisions (resolve before Phase 2 of the roadmap)

1. **Lesson graduation threshold.** Currently 5 reinforcements. Is 5 right?
   Needs calibration against real data once the system has run for a few weeks.
2. **Lesson wording.** How terse vs. how descriptive? The coach prompt says
   "short, e.g. `check empty input before binary search`" — confirm this is
   the right shape after seeing 5-10 real lessons.
3. **~~SearXNG as YouTube API replacement.~~** **Resolved 2026-07-28: yes,
   replace.** The YouTube Data API key is dropped entirely. SearXNG
   (`engines=youtube` JSON API) is the sole YouTube search backend. Reason:
   one fewer Google API key to manage, no quota concerns at 1 search/day,
   leverages existing homelab infrastructure. The YouTube Data API code path
   is deleted, not kept as a fallback.
4. **~~Browserless for LeetCode GraphQL.~~** **Resolved 2026-07-28: yes,
   primary path.** Browserless is the primary (and only) path for all
   LeetCode GraphQL calls; the direct httpx code path is removed. Reason:
   Cloudflare's 2026 bot detection blocks unauthenticated programmatic
   GraphQL from datacenter/homelab IPs as a matter of course, not
   hypothetically. Running the same `fetch()` from within a real Chrome
   page context (Browserless `/function`) is the robust default. If
   Browserless is unavailable, fail loudly with `LeetCodeFetchError`.
5. **~~Whether to keep the Google Tasks integration at all.~~**
   **Resolved 2026-07-31: removed from v1.** The original n8n v3 workflow
   mirrored each chosen problem into Google Tasks via GCP OAuth. That
   integration is **not ported** to the Python app: it added an external
   API surface and an OAuth refresh-token flow whose 7-day expiry was a
   recurring source of manual re-auth, for no user-facing value (the
   coach feedback already lives in the Telegram reply). Coach feedback
   is delivered via the Telegram reply instead of Google Task notes.
   The original n8n behavior is preserved in
   `n8n-reference/workflows/flow-b-telegram-and-coach.json` for the
   historical record.
