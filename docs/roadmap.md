# Roadmap — LeetCode Coach Service

Status: plan | Owner: Vlad | Last revised: 2026-07-26
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

- [ ] `integrations/telegram.py` — `set_webhook`, `send_message`,
      `send_reply`. Retries on 5xx/timeout.
- [ ] `integrations/llm.py` — `LLMClient` with primary + fallback per
      `architecture.md` §5. Typed `LLMResponse` with `tokens_in`, `tokens_out`.
- [ ] `integrations/google_tasks.py` — `create_task`, `update_task`,
      `mark_complete` (with `notes_append`, **not** replace — this fixes the
      n8n bug where notes were dropped on completion). Typed
      `GoogleAuthExpiredError` on `invalid_grant`.
- [ ] `integrations/leetcode.py` — `refresh_pool()` GraphQL pull. Stub the
      Browserless fallback (log "GraphQL failed, Browserless not configured"
      and re-raise for now).
- [ ] `integrations/youtube.py` — `search_walkthroughs(title)`. Optional;
      raises `YouTubeDisabled` if `YOUTUBE_API_KEY` is unset.
- [ ] `errors.py` — typed exception hierarchy + `send_alert(message)` that
      posts to Telegram.
- [ ] Tests for each client with `respx` mocks. Specifically:
      `test_llm_fallback.py` (primary 500 → fallback fires),
      `test_google_auth_branch.py` (`invalid_grant` → `GoogleAuthExpiredError`
      → alert message, **not** a generic crash).

**Exit criteria:** every client has a passing test suite. The Google auth
branch is verified to send the distinct alert message, not a generic crash.

## Phase 2 — Flow A (daily candidates) (1 session)

**Goal:** the morning proposal actually fires and sends a Telegram message.

- [ ] `prompts/propose.py` — system + user prompt, ported verbatim from
      `n8n-reference/workflows/flow-a-schedule-and-expiry.json` AI Agent node
      `text` field.
- [ ] `flows/flow_a.py` — `propose_5()`:
      1. Read recent log (30 rows), unsolved problems, active lessons.
      2. Call `LLMClient.complete(propose.prompt)`.
      3. Parse JSON response (`candidate_list_markdown` + `candidates`).
      4. Send the markdown to Telegram.
- [ ] `scheduling/cron.py` — APScheduler job for `5 9 * * *` Europe/Bucharest.
- [ ] **Fix the n8n bug:** the unsolved problem pool is read from
      `leetcode_problems WHERE solved = false` and passed into the prompt.
      The n8n version forgot this and only fetched `solved = true`.
- [ ] Tests: `test_flow_a.py` with a mocked `LLMClient` returning a canned
      5-candidate JSON. Asserts the Telegram send is called with the
      markdown, and that the unsolved pool is actually passed to the prompt
      (regression test for the n8n bug).

**Exit criteria:** manually trigger `propose_5()` locally → Telegram receives
a 5-candidate message with reasoning per candidate. The unsolved-pool
regression test passes.

## Phase 3 — Flow B (reply router + coach pass) (2 sessions)

**Goal:** replying to the 5-list message with "2 5" creates 2 per-problem
threads + 2 Google Tasks + 2 `pending_review` rows; replying to a per-problem
message with code triggers the coach pass and closes the loop.

### Phase 3a — Pick-parse path

- [ ] `webhooks/telegram.py` — `POST /telegram/webhook` →
      `flow_b.handle_update(update)`.
- [ ] `flows/flow_b.py` — correlation logic:
      - `update.message.reply_to_message` present → look up
        `pending_review` by `message_id`.
      - Not present → fuzzy match against today's open rows.
      - Zero/multiple fuzzy matches → clarification prompt, stop.
- [ ] Pick parse: regex `\d+`, cap at 2, map to today's 5-list candidates.
      (The 5-list candidate array must be persisted somewhere Flow B can
      read it — store it in a `daily_candidates` table or in
      `pending_review` rows pre-inserted with `status = proposed`. Decide
      in this phase; document the choice.)
- [ ] For each pick: send per-problem Telegram message (capture
      `message_id`), create Google Task (capture `task_id`), insert
      `pending_review` row.
- [ ] Tests: `test_flow_b.py` pick-parse path with mocked Telegram + Google
      Tasks. Asserts 2 messages, 2 tasks, 2 rows.

### Phase 3b — Coach pass path

- [ ] `prompts/coach.py` — system + user prompt, ported verbatim from
      `n8n-reference/workflows/flow-b-telegram-and-coach.json` AI Agent node
      `text` field (the long one with the 5 coaching dimensions + lesson
      decision instructions).
- [ ] Coach pass: call `LLMClient`, parse the structured response
      (`tutor_feedback`, `lesson_title`, `lesson_category`,
      `lesson_should_graduate`, `status`, `time_spent_min`).
- [ ] Lesson decision (`Code (lesson decision)` equivalent):
      double-gated graduation per FR-2.6.
- [ ] Post-coach updates:
      1. Insert `leetcode_log` row.
      2. If solved → mark `leetcode_problems.solved = true`.
      3. **Fix the n8n bug:** `google_tasks.mark_complete(task_id,
         notes_append=tutor_feedback)` — append, don't replace.
      4. Update `pending_review.status = done`.
      5. Send Telegram confirmation naming any lesson saved/reinforced/retired.
- [ ] Tests: `test_flow_b.py` coach path with mocked LLM returning a canned
      coach response. Asserts all 5 post-coach updates happen in order.
      Golden-output test for the lesson-decision double gate (coach says
      graduate but DB count is 4 → bump, not graduate).

**Exit criteria:** end-to-end local test — send a fake "2 5" reply → 2
per-problem messages appear → reply to one with fake code → coach feedback
appears → DB shows `pending_review.status = done`, `leetcode_log` row
inserted, Google Task marked complete with feedback in notes.

## Phase 4 — Expiry sweep (0.5 session)

**Goal:** the 05:05 sweep marks un-answered problems expired and sends the
summary.

- [ ] `flows/expiry.py` — `sweep_expired()`:
      1. Select today's `pending_review` where `status = open`.
      2. For each: set `status = expired`, update Google Task notes with
         "Expired without reply on <date>" (don't delete).
      3. Send one Telegram summary message.
- [ ] APScheduler job for `5 5 * * *`.
- [ ] Tests: `test_expiry.py` with 0, 1, and 2 open rows.

**Exit criteria:** manually trigger `sweep_expired()` with 2 open rows in
the DB → both marked expired, Google Tasks updated, one summary message
sent.

## Phase 5 — Weekly LeetCode refresh (0.5 session)

**Goal:** the problem pool stays current without manual intervention.

- [ ] `integrations/leetcode.py` `refresh_pool()` — hit LeetCode GraphQL,
      upsert into `leetcode_problems`.
- [ ] APScheduler job for `0 3 * * 1` (Monday 03:00).
- [ ] Tests: `test_leetcode_refresh.py` with a mocked GraphQL response.

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
- [ ] Flip GCP OAuth consent screen from `Testing` to `In production` (per
      `n8n-reference/README.md` lines 325-353) and re-authenticate the
      Google credential one final time.
- [ ] Wait for 09:05 the next morning. Real proposal should arrive.

**Exit criteria:** one full real day — morning proposal, user picks 2,
sends code for one, gets coach feedback, the other expires at 05:05 the
next day. All four tables have real rows. Cost log shows <$0.20 for the day.

## Phase 7 — Hardening (ongoing, post-v1)

- [ ] Calibrate the lesson graduation threshold (open decision §8.1 in
      `business-requirements.md`) after 2-3 weeks of real data.
- [ ] If YouTube Data API quota becomes a problem → wire SearXNG fallback.
- [ ] If LeetCode GraphQL blocks the homelab IP → wire Browserless fallback.
- [ ] If Google Tasks causes more ops pain than value → consider dropping
      it (open decision §8.5).
- [ ] Structured log → Loki → Grafana dashboard if observability needs grow.
- [ ] Golden-output test suite for the coach pass: collect 10 real coach
      responses, manually rate them, lock them as regression baselines.

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
Phase 6 is the deploy gate. Phase 7 is open-ended.

The two n8n business-logic bugs (missing unsolved pool, missing Google Task
notes append) are fixed in the phase where the relevant code is written —
Phase 2 and Phase 3b respectively — not as a separate "fix" phase. The three
n8n error-handling gaps (missing retry on Data Table, missing Google auth
branch, missing Telegram Trigger onError) are free in Python: retries are
default-on in `tenacity`, the Google auth branch is a typed exception, and
the Telegram webhook handler is a normal FastAPI route with normal error
handling.
