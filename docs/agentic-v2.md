# Agentic LeetCode Coach V2

Status: active contract | Revised: 2026-08-16

This document supersedes conflicting V1 behavioral and architecture rules. V1
documents remain historical context, not constraints on the current agent.

## Role and learning loop

One `gpt-5.6-terra` coach is practical, encouraging, honest, and focused on one
learner's interview readiness. It assesses the profile and submitted work,
chooses practice that targets weaknesses, reviews correctness, complexity, and
reusable patterns, extracts or reinforces lessons, then adapts future work.

The prompt is a coaching brief, not a procedural workflow. Terra decides whether
the useful next step is practice, review, explanation, memory, or follow-up. It
may choose one or many problems, revisit solved work, and record user-reported
work without a pre-existing queue. Empty catalog state or unavailable lookup does
not veto an exact user-supplied LeetCode identity.

Code owns only mechanical invariants: normalized exact slug/URL identity, atomic
transactions, replay idempotency, per-chat isolation, deterministic escaped
rendering, transport bounds, and bounded execution. Reads may run in parallel;
writes are serialized. Terra has 16 turns by default and at most 32. It may call
the tool-less, read-only Sol advisor once per run, including after a write.

The volatile prompt suffix includes today's Europe/Bucharest date. Durable
coaching memory stores versioned, bounded goals, preferences, availability,
curriculum, mastery, and notes in bot state.

## Tools and state

Reads include profile, flexible catalog search (`eligible_unsolved`, `solved`,
`ineligible`, or `all`), exact problem lookup, queue, progress, coaching memory,
attempt history, and follow-ups. `start_problem` atomically inserts missing exact
metadata when supplied and creates—or replays—the existing open review.

Writes include `publish_practice_set`, picks, attempts, lessons, memory updates,
attempt correction/reversal, and follow-up scheduling/cancellation. They execute
immediately through the per-chat write lock. There is no live approval, yes/no,
resume, or persisted SDK interruption flow. Historical approval tables may remain
in an old database but are inert.

`publish_practice_set` accepts 1–20 candidates. Twenty is a mechanical Telegram
controller limit, not a pedagogical mix or count rule. No eligibility, solved-state,
difficulty-mix, exact-five, or open-review policy gates the coach. Informational
proposal pages are sent at least once under Telegram's 4,096-character limit;
active selection controls appear only after all pages. Callback identities make
toggle replay stable, and Done resumes any missing review deliveries.

Attempts support `solved` (1 credit), `reviewed` (0.5), `saw_solution` (0.25),
`attempted` (0), and `skipped` (0), plus language, time, solution summary,
feedback, and today/past effective date. `attempted` leaves its review open.
Corrections and reversals append revisions and compensating ledger entries, then
recompute problem aggregates from the migration baseline and active attempts.
`solved` remains true when externally verified or any active solved attempt exists.

Follow-ups persist Bucharest wall times as UTC. The scheduler checks each minute
and delivers at least once; a crash after Telegram accepts a send but before the
database checkpoint can duplicate that message. Morning coaching runs
unconditionally and lets Terra decide what action is useful.

Hint and Why buttons invoke Terra. Hints progress replay-safely from conceptual,
to invariant/next step, to pseudocode. Callback replay does not advance the level.

## Migration and proof

`v2_0002` widens bot state, adds attempt metadata/reversal/audit state, preserves
problem aggregate baselines, links credit entries to attempts, and adds durable
follow-ups. Deployment remains FastAPI + PostgreSQL + APScheduler in
Europe/Bucharest; Redis, Celery, workers, multi-user support, and a web UI remain
out of scope.

Automated tests are the current evidence for this autonomy release. The paid
real-model proof has not been rerun; `docs/live-proof.md` records that boundary.
