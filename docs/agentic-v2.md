# Agentic LeetCode Coach V2

Status: canonical V2 contract and design | Owner: Vlad | Revised: 2026-08-03

This document supersedes the marked V1 requirements, architecture, and roadmap
sections. It deliberately replaces V1's “no LLM tool-calling loop” decision.
The V1 documents and `n8n-reference/` remain historical evidence only.

## Product boundary

V2 remains a single-user Telegram coach, FastAPI service, PostgreSQL database,
Alembic migration set, APScheduler scheduler, Docker deployment, and
Europe/Bucharest operating timezone. It preserves daily proposals/refill,
natural-language and button picks, coaching, lesson progression, status, hints,
explanations, reattempts, follow-ups, credits, tax, nudges, expiry, and extend.

Out of scope: multi-user support, web UI, Redis, Celery, task queues,
multi-agent handoffs, Gemini/fallback providers, and model-generated Telegram
HTML or Markdown.

## Controller and bounded execution

One `gpt-5.6-terra` agent controls each Telegram conversation through the
OpenAI Agents SDK. Agent runs are serialized per chat and Telegram `update_id`
delivery is idempotent. A run is limited to eight model turns, three concurrent
read tools maximum, serial writes, bounded tool payloads, and explicit tool
timeouts. Provider failures retry only when transient, then fail visibly; the
system never fabricates a result or durable state.

Terra may call `ask_sol_advisor` once per run for difficult coaching,
consequential ambiguity, repeated schema failure, or an explicit request. The
tool makes exactly one read-only `gpt-5.6-sol` request and returns
`recommendation`, `risks`, `missing_evidence`, and `suggested_next_action`.
Its advice is untrusted. Sol receives no tools, cannot mutate state, cannot
approve a write, and cannot resume a paused run. Escalation is forbidden after
a write starts or while an approval is pending.

Use explicit prompt caching for Terra's stable instructions and tool schemas;
place chat, profile, and tool-result data after the cache breakpoint. Record
model, turns, tool calls, latency, cache reads/writes, input/output tokens,
and Sol escalation reason for every run.

## Canonical data and rendering

PostgreSQL is canonical for problem slug, title, URL, tags, difficulty, solved
state, and eligibility. The model may choose slugs and provide teaching text;
code hydrates all displayed metadata. Reject unknown, duplicate, solved,
ineligible, wrong-mix, or model-invented selections before persistence or
Telegram output. `House Robber` is canonical easy and must never be stored or
shown as hard.

Models return plain text or typed data only. Code renders proposal cards as
deterministic escaped HTML and uses a dedicated renderer where code needs HTML;
ordinary conversation is plain text. No user sees MarkdownV2 escape artifacts.

## Tools and durable actions

Read tools are narrowly typed: `get_learning_profile`, `search_problem_pool`,
`get_problem`, `get_open_queue`, `get_progress`, `get_walkthroughs`,
`draft_proposal`, and `ask_sol_advisor`. `draft_proposal` validates selection
and required difficulty mix, hydrates canonical fields, and produces the
deterministic preview. Persisting that preview as an unsent proposal batch is
pre-authorized operational staging, not a user-learning or outcome mutation.
V2 has no Browserless or SearXNG dependency. `get_walkthroughs` remains a
bounded empty result until a first-party tutorial source is adopted; canonical
problem refresh calls LeetCode directly and rejects non-exact slug matches.

Write tools are atomic domain operations and accept identifiers plus confirmed
outcomes, never model-supplied problem metadata: `commit_picks`,
`commit_attempt`, `commit_canonical_attempt`, `skip_problem`, `mark_solution_viewed`, `reattempt_problem`,
`extend_proposal`, `accept_credit_deficit`, and `adjust_lesson`.

`commit_canonical_attempt` records verified work against any exact canonical slug,
including already-solved or currently ineligible problems. It closes and links the
oldest matching open review when one exists; otherwise it creates an attempt without
a review. It never fabricates a proposal or queue item. The agent must verify the
slug with `get_problem`, ask for a slug or LeetCode URL when identity is unclear, and
must not refuse verified work merely because the queue or eligible pool is empty.
The trusted SDK approval call ID supplies an internal idempotency key that is never
part of the model-facing tool schema. Replaying the same approved call is a no-op;
a separately approved attempt remains valid even for the same canonical problem.

Natural-language requests for a durable user-driven write pause in persisted
human-in-the-loop approval state and show an action summary with Approve/Reject
buttons. Exact `yes`/`no` text is valid only as a reply to that approval or
when exactly one approval is pending. An explicit action button is confirmation
only for its exact operation. Pending approvals expire after 24 hours; stale
buttons/replies are harmless. Scheduled tax, expiry, operational state,
conversation storage, and idempotency records are pre-authorized system work.

## Schema and migration

V2 uses a fresh database/schema: canonical problems, attempts, lessons,
proposal batches, pending reviews, credit ledger, bot state, processed Telegram
updates, conversation items, agent runs, and pending approvals. The migration
imports only canonical LeetCode problems, attempt history, and tutor lessons.
It drops V1 callback tokens, proposals, reviews, agent/runtime state, and all
operational records; V2 credit balance begins at zero. Tests compare source and
target counts and representative records while proving excluded data is absent.

## Scheduler, deployment, and cutover

APScheduler owns scheduled work in Europe/Bucharest. V2 uses the existing
FastAPI, Telegram, PostgreSQL, Alembic, Docker topology, with one scheduler
leader guarded by PostgreSQL. Validate locally with the terminal simulator,
real-model proof harness, and request-level Telegram Bot API contract tests;
staging is outside the current delivery scope. Before production cutover, verify the deployed SHA: raw
MarkdownV2 output despite the current HTML fix means stale deployment.

Import learning data into a fresh V2 production database, switch the webhook,
then enable the scheduler. Keep V1 service/database read-only for seven days as
rollback, then remove them only after a stable observability review.

## Acceptance requirements

- Model-supplied hard `House Robber` is canonicalized as easy or rejected by
  mix validation; invented metadata never reaches storage or Telegram.
- Approval, rejection, contextual yes/no, ambiguous text, expiry, restart,
  stale callback, and duplicate update behavior are deterministic.
- Rejected/expired writes leave domain state unchanged; replayed updates never
  repeat writes.
- Terra calls Sol no more than once; Sol has no write path or approval bypass.
- Cache telemetry, max turns, timeouts, malformed tool input, provider outage,
  and partial Telegram failure are tested and fail loudly.
- End-to-end tests cover proposal → pick → coach → lesson → credits plus skip,
  saw solution, reattempt, nudge, expiry, and extend.
