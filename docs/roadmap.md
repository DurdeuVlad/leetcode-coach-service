# Roadmap — LeetCode Coach Service

Status: plan | Owner: Vlad | Last revised: 2026-07-30
Companion to `business-requirements.md` (the contract) and `architecture.md`
(the design). Phased so each phase ships something testable on its own.

## Guiding principles

- **Port the spec, don't redesign it.** The n8n README is a complete behavioral
  spec. Prompts carry over verbatim. Flow logic carries over as functions.
  The only redesign is the runtime.
- **Each phase ends with a working thing on the homelab.** No phase is "scaffold
  only." If a phase can't be deployed, it's too big.
- **Tests come with the code, not after.** The coach pass prompt is the highest-
  risk piece (LLM output is non-deterministic); it gets a golden-output test
  suite in Phase 2.
- **No Browserless / SearXNG in v1.** Per the user's call: only if a primary
  API actually fails. The integration points are documented; the code is stubs.

## Phase 0 — Project bootstrap (1 session)

**Goal:** empty skeleton that boots, connects to Postgres, and serves a 200
on `/health`.

- [ ] `pyproject.toml` with all deps from `architecture.md` §2.
- [ ] `src/leetcode_coach/main.py` — FastAPI app + lifespan + `/health`.
- [ ] `src/leetcode_coach/config.py` — pydantic-settings, fails fast on
      missing required env vars.
- [ ] `src/leetcode_coach/db/base.py` + `models.py` — 4 SQLModel tables.
- [ ] `alembic/` — initial migration creating all 4 tables.
- [ ] `Dockerfile` (multi-stage, uv-based).
- [ ] `docker-compose.yml` — app + postgres for local dev.
- [ ] `.env.example` — all env vars from `architecture.md` §8.
- [ ] `tests/conftest.py` — testcontainers-postgres fixture.
- [ ] One smoke test: app boots, `/health` returns 200, DB is reachable.

**Exit criteria:** `docker compose up` → app + postgres healthy, `/health`
returns 200, `alembic upgrade head` creates all 4 tables.

## Phase 1 — Integrations (1-2 sessions)

**Goal:** every external service is reachable through a typed client with
retries and typed errors. No flow logic yet.

- [x] `integrations/telegram.py` — `set_webhook`, `send_message`,
      `send_reply`. Retries on 5xx/timeout.
- [x] `integrations/llm.py` — `LLMClient` with primary + fallback per
      `architecture.md` §5. Typed `LLMResponse` with `tokens_in`, `tokens_out`.
- [x] `integrations/leetcode.py` — `refresh_pool()` GraphQL pull via the
      homelab Browserless `/function` endpoint (primary path per the
      2026-07-28 decision — `docs/business-requirements.md` §8 #4).
- [x] `integrations/youtube.py` — `search_walkthroughs(title)` via the
      homelab SearXNG `engines=youtube` JSON API; raises `YouTubeDisabled`
      if `SEARXNG_URL` is unset (per the 2026-07-28 decision —
      `docs/business-requirements.md` §8 #3).
- [x] `errors.py` — typed exception hierarchy + `send_alert(message)` that
      posts to Telegram.
- [x] Tests for each client with `respx` mocks. Specifically:
      `test_llm_fallback.py` (primary 500 → fallback fires).

**Exit criteria:** every client has a passing test suite.
✅ Met — Phase 1 tests green (`test_telegram`, `test_llm_fallback`,
`test_leetcode`, `test_youtube`). The
`test_smoke.py::test_health_ok` failure is a pre-existing Phase 0
infrastructure test requiring a live Postgres at `localhost:5432`, unrelated
to Phase 1.

## Phase 2 — Flow A (daily candidates) (1 session)

**Goal:** the morning proposal actually fires and sends a Telegram message.

- [x] `prompts/propose.py` — system + user prompt, ported verbatim from
      `n8n-reference/workflows/flow-a-schedule-and-expiry.json` AI Agent node
      `text` field.
- [x] `flows/flow_a.py` — `propose_5()`:
      1. Read recent log (30 rows), unsolved problems, active lessons.
      2. Call `LLMClient.complete(propose.prompt)`.
      3. Parse JSON response (`candidate_list_markdown` + `candidates`).
      4. Send the markdown to Telegram.
- [x] `scheduling/cron.py` — APScheduler job for `5 9 * * *` Europe/Bucharest.
- [x] **Fix the n8n bug:** the unsolved problem pool is read from
      `leetcode_problems WHERE solved = false` and passed into the prompt.
      The n8n version forgot this and only fetched `solved = true`.
- [x] Tests: `test_flow_a.py` with a mocked `LLMClient` returning a canned
      5-candidate JSON. Asserts the Telegram send is called with the
      markdown, and that the unsolved pool is actually passed to the prompt
      (regression test for the n8n bug).

**Exit criteria:** manually trigger `propose_5()` locally → Telegram receives
a 5-candidate message with reasoning per candidate. The unsolved-pool
regression test passes.
✅ Met — 19/19 Flow A tests green (`test_flow_a.py`): BUG-1 regression
(`_gather_data` reads `solved=false`, `_build_prompt` includes unsolved pool),
parse/validate edge cases, full end-to-end flow with mocked LLM + Telegram.
Full suite: 47/47 green.

## Phase 3 — Flow B (reply router + coach pass) (2 sessions)

**Goal:** replying to the 5-list message with "2 5" creates 2 per-problem
threads + 2 `pending_review` rows; replying to a per-problem
message with code triggers the coach pass and closes the loop. (Google
Tasks removed 2026-07-31 — see `business-requirements.md` §8 decision 5.)

### Phase 3a — Pick-parse path

- [x] `webhooks/telegram.py` — `POST /telegram/webhook` →
      `flow_b.handle_update(update)`.
- [x] `flows/flow_b.py` — correlation logic:
      - `update.message.reply_to_message` present → look up
        `pending_review` by `message_id`.
      - Not present → fuzzy match against today's open rows.
      - Zero/multiple fuzzy matches → clarification prompt, stop.
- [x] Pick parse: regex `\d+`, cap at 2, map to today's 5-list candidates.
      (The 5-list candidate array must be persisted somewhere Flow B can
      read it — store it in a `daily_candidates` table or in
      `pending_review` rows pre-inserted with `status = proposed`. Decide
      in this phase; document the choice.)
- [x] For each pick: send per-problem Telegram message (capture
      `message_id`), insert `pending_review` row. (Google Task creation
      removed 2026-07-31.)
- [x] Tests: `test_flow_b.py` pick-parse path with mocked Telegram.
      Asserts 2 messages, 2 rows.

### Phase 3b — Coach pass path

- [x] `prompts/coach.py` — system + user prompt, ported verbatim from
      `n8n-reference/workflows/flow-b-telegram-and-coach.json` AI Agent node
      `text` field (the long one with the 5 coaching dimensions + lesson
      decision instructions).
- [x] Coach pass: call `LLMClient`, parse the structured response
      (`tutor_feedback`, `lesson_title`, `lesson_category`,
      `lesson_should_graduate`, `status`, `time_spent_min`).
- [x] Lesson decision (`Code (lesson decision)` equivalent):
      double-gated graduation per FR-2.6.
- [x] Post-coach updates:
      1. Insert `leetcode_log` row.
      2. If solved → mark `leetcode_problems.solved = true`.
      3. Update `pending_review.status = done`.
      4. Send Telegram confirmation naming any lesson saved/reinforced/retired.
- [x] Tests: `test_flow_b.py` coach path with mocked LLM returning a canned
      coach response. Asserts all post-coach updates happen in order.
      Golden-output test for the lesson-decision double gate (coach says
      graduate but DB count is 4 → bump, not graduate).

**Exit criteria:** end-to-end local test — send a fake "2 5" reply → 2
per-problem messages appear → reply to one with fake code → coach feedback
appears → DB shows `pending_review.status = done`, `leetcode_log` row
inserted.

## Phase 4 — Expiry sweep (0.5 session)

**Goal:** the 05:05 sweep marks un-answered problems expired and sends the
summary.

- [x] `flows/expiry.py` — `sweep_expired()`:
      1. Select today's `pending_review` where `status = open`.
      2. For each: set `status = expired`.
      3. Send one Telegram summary message.
- [x] APScheduler job for `5 5 * * *`.
- [x] Tests: `test_expiry.py` with 0, 1, and 2 open rows.

**Exit criteria:** manually trigger `sweep_expired()` with 2 open rows in
the DB → both marked expired, one summary message sent.

## Phase 5 — Weekly LeetCode refresh (0.5 session)

**Goal:** the problem pool stays current without manual intervention.

- [x] `integrations/leetcode.py` `refresh_pool()` — hit LeetCode GraphQL,
      upsert into `leetcode_problems`.
- [x] APScheduler job for `0 3 * * 1` (Monday 03:00).
- [x] Tests: `test_leetcode_refresh.py` with a mocked GraphQL response.

**Exit criteria:** manually trigger `refresh_pool()` → `leetcode_problems`
has the test fixture's rows.

## Phase 6 — Deploy to Coolify (1 session)

**Goal:** running on the homelab, webhook live, first real proposal arrives
the next morning.

- [ ] Push to a private GitHub repo.
- [ ] Coolify: create Postgres service, note the connection string.
- [ ] Coolify: create app service from the repo, set all env vars from
      `.env.example`, set `DATABASE_URL` to the Postgres service.
- [ ] First deploy: `alembic upgrade head` runs on startup.
- [ ] Verify `/health` is 200 from the public URL.
- [ ] Verify Telegram `setWebhook` succeeded (the app logs the response).
- [ ] Send a test message to the bot from Telegram → app logs the update.
- [ ] Wait for 09:05 the next morning. Real proposal should arrive.

**Exit criteria:** one full real day — morning proposal, user picks 2,
sends code for one, gets coach feedback, the other expires at 05:05 the
next day. All four tables have real rows. Cost log shows <$0.20 for the day.

## Phase 7 — Hardening (ongoing, post-v1)

- [ ] Calibrate the lesson graduation threshold (open decision §8.1 in
      `business-requirements.md`) after 2-3 weeks of real data.
- [x] ~~If YouTube Data API quota becomes a problem → wire SearXNG fallback.~~
      **Resolved 2026-07-28:** SearXNG is now the primary YouTube backend;
      YouTube Data API dropped entirely (§8.3).
- [x] ~~If LeetCode GraphQL blocks the homelab IP → wire Browserless fallback.~~
      **Resolved 2026-07-28:** Browserless is now the primary (and only)
      path for LeetCode GraphQL (§8.4).
- [ ] Structured log → Loki → Grafana dashboard if observability needs grow.
- [ ] Golden-output test suite for the coach pass: collect 10 real coach
      responses, manually rate them, lock them as regression baselines.

## Phase 8 — Interactive control + progression visibility (1-2 sessions)

Makes the bot interactive on Telegram (slash commands), surfaces the
adaptability loop to the user (progression queries), and keeps a live
pinned snapshot. Depends on Phase 3 (Flow B) and the admin API (merged
on dev as of 2026-07-29) — the `dry_run`-capable flow internals are the
template for the command handlers.

### Phase 8a — Slash commands (FR-6)
- [x] Command router: parse `/`-prefixed messages before FR-2.2 reply
      correlation (`flow_b.handle_update`).
- [x] `/propose` → call `flow_a.propose_5(dry_run=False)`.
- [x] `/pick <n1> [<n2>]` → call `flow_b._pick_parse_path` with the parsed
      indices.
- [x] `/coach <text>` (and `/coach <slug> <text>` form) → call
      `flow_b._coach_pass_path` + `_post_coach_updates`.
- [x] Unknown command → short "unknown command" reply, no LLM, no DB write.
- [x] Tests: command router dispatch, each command path, unknown command,
      non-allowlisted chat rejected.

### Phase 8b — Progression queries (FR-7)
- [x] `/status` → deterministic text dump (active lessons, last 7 days of
      `leetcode_log`, current streak). No LLM call.
- [x] `/why <slug>` → single LLM call, 2-3 sentences on why a problem was
      proposed / what lesson it targets.
- [x] Tests: `/status` output shape against seeded DB; `/why` mocked LLM
      single-call bound.

### Phase 8c — Pinned progression message (FR-8)
- [x] `bot_state` table + Alembic migration (key/value/updated_at).
- [x] `integrations/telegram.py`: `edit_message_text`, `pin_message`,
      `unpin_message` helpers.
- [x] Snapshot builder: today's status counts, active lessons count,
      current streak.
- [x] Refresh hook called after Flow A run, Flow B pick, Flow B coach.
- [x] Recovery: if `editMessageText` fails, create + pin a new message and
      store the new ID in `bot_state`.
- [x] Tests: snapshot builder, refresh hook, recovery path.

**Exit criteria:** from Telegram, `/propose` → `/pick 1` → `/coach <code>`
runs the full pipeline; `/status` shows the just-coached attempt; the pinned
message updates after each step. All tests green.
✅ Met — merged in `e6380a0` (PR #49). 137/137 tests green. Slash commands
(`/propose`, `/pick`, `/coach`, `/status`, `/why`), pinned progression
message with recovery path, and HTTP request logging all live on master.

## Phase 9a — Scheduler service topology

- [x] Run `app` and `scheduler` as separate services from the same image.
- [x] Restrict APScheduler startup to `python -m leetcode_coach.scheduler`.
- [x] Guard job registration with a PostgreSQL advisory lock and keep a
      non-leader replica idle.
- [x] Move the canonical timed slots to 00:00 tax, 09:05 refill, 20:00
      nudge, 22:00 expiry, and Monday 03:00 refresh (Europe/Bucharest).

The scheduler calls future credit/nudge/state-machine flows lazily so this
topology can deploy before those implementation issues land.

## Phase 9 — Inline UI + credit/debit budget system (planning)

**Goal:** replace the text-only 5-list with inline-button cards, add a
credit/debit budget system that rewards engagement, and redesign expiry
as user-controlled via buttons. Planned in `plan/PHASE9_DESIGN.md`
(issues #040–#049).

**Status:** planning only — implementation has not started. The design
doc and issues live in `plan/` as the execution backlog. The
credit/tax/timing values in the design are **provisional defaults pending
Phase 7 calibration** (see `business-requirements.md` §8); they are not
fixed decisions and must be tuned with real runtime data before being
treated as final.

- [ ] `credit_ledger` table + Alembic migration (issue #040).
- [ ] Credit calculation + `format_balance` helper (issue #041).
- [ ] Scheduler jobs: daily tax (midnight), queue refill check (replaces
      09:05 propose), nudge at 20:00 (issue #042).
- [ ] Callback query handler infrastructure + `answer_callback_query` +
      `edit_message_reply_markup` wrappers (issue #043).
- [ ] Propose card UI: inline buttons, capture `propose_message_id`
      (issue #044).
- [ ] Per-problem thread action buttons: pick/cancel/skip/hint/solution/why
      (issue #045).
- [ ] Coach followup buttons: next/reattempt/why-lesson (issue #046).
- [ ] Nudge flow with inline buttons (issue #047).
- [ ] Pinned message credits display (issue #048).
- [ ] Expiry redesign: 22:00 sweep with [Extend to Tomorrow] button, no
      status change / summary message (issue #049).

**Exit criteria:** inline buttons render correctly on Telegram; credit
balance updates after every solve/review/saw-solution/skip; nudge fires
at 20:00 when balance < 0; expiry offers [Extend] instead of auto-expiring.
All tests green.

## What we explicitly defer

- Multi-user support (out of scope §7).
- Web UI (out of scope §7).
- Photo/image evidence of solutions (out of scope §7).
- LLM tool-calling loop in the coach pass (`architecture.md` §12).
- Anki export of lessons (out of scope §7).
- Automated mock interviews (handled separately in `MOONSHOT-PLAN.md`).

## Sequencing rationale

Phases 0-1 are infrastructure that every later phase depends on. Phases 2-4
are the three flows in dependency order: Flow A must exist before Flow B's
pick-parse path can be tested (Flow B reads Flow A's candidate list); Flow B
must exist before the expiry sweep is meaningful (expiry acts on
`pending_review` rows Flow B creates). Phase 5 is independent and small.
Phase 6 is the deploy gate. Phase 7 is open-ended. Phase 9 (inline UI +
credit/budget system) builds on the Phase 8 command router and pinned
message; its planning lives in `plan/PHASE9_DESIGN.md`.

The two n8n business-logic bugs (missing unsolved pool, ~~missing Google Task
notes append~~) are fixed in the phase where the relevant code is written —
Phase 2 and Phase 3b respectively — not as a separate "fix" phase. (The
Google Task notes-append bug is now moot — the integration was removed
2026-07-31; see `business-requirements.md` §8 decision 5.) The three
n8n error-handling gaps (missing retry on Data Table, ~~missing Google auth
branch~~, missing Telegram Trigger onError) are free in Python: retries are
default-on in `tenacity`, ~~the Google auth branch is a typed exception~~
(removed with the integration), and the Telegram webhook handler is a normal
FastAPI route with normal error handling.
