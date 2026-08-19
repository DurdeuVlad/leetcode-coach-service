# Changelog

## 2026-08-16 — Autonomous coaching runtime

- Remove live approval/resume state and execute explicit writes immediately through
  the per-chat serialized write boundary.
- Add flexible catalog search, exact `start_problem`, durable versioned coaching
  memory, and 16-turn default / 32-turn maximum execution.
- Add five attempt outcomes with language, time, and solution summary; searchable
  history; idempotent correction/reversal audit records; compensating credits; and
  aggregate recomputation from preserved migration baselines.
- Add Bucharest-aware dates, accurate unbounded streak calculation, durable UTC
  follow-ups with minute delivery, unconditional morning coaching, and Terra-driven
  progressive Hint/Why callbacks.
- Add Alembic `v2_0002`. Automated verification is local; paid live-model proof was
  intentionally not run for this build.

## 2026-08-16 — Coach autonomy and flexible problem identity

- Replace the Terra procedural rulebook with a lean role, personality, and
  curriculum playbook while preserving the adaptive learning loop.
- Add `record_problem_attempt` with exact slug/URL normalization, atomic metadata
  upsert, queue-free recording, and optional today/past attempt dates.
- Apply backdated dates to both attempts and credit while preserving the latest
  `last_attempted` value and all existing durable problem state.
- Let proposals contain one or more agent-chosen candidates and remove rigid
  difficulty, eligibility, solved-state, selection-count, and open-review caps.
- Add callback-idempotent toggle + Done selection and checkpointed at-least-once
  informational pagination under Telegram's 4,096-visible-character limit; active
  controls appear only after all pages are delivered.
- Bound proposal transport to 20 candidates and expose problem metadata length limits
  in the tool schema before writes.
- Add automated regression coverage for the supplied empty-registry transcript.
  The live-model proof has not been rerun for this change.

## 2026-08-15 — Gated production delivery

- Reuse the full CI workflow as a required deploy prerequisite for every `master` release.
- Keep direct CI triggers on `dev` while removing the independent, unordered `master` CI run.
- Add a high-safety production verification prompt with evidence-gated CI/CD-only repairs.

## 2026-08-15 — Deterministic work receipts

- Send a database-authoritative receipt for every recorded or replayed attempt before coaching.
- Show canonical title, result, earned credit, balance transition, and queue/direct path.
- Keep multi-attempt receipts ordered and make replays visibly earn `+0.00` at the current balance.

## 2026-08-15 — Direct coaching actions

- Resolve exact LeetCode slugs on demand so valid solved work can be recorded even when the bounded local registry misses it.
- Execute explicit coaching actions without approval pauses while keeping canonical validation and retry-safe mutations.
- Let new instructions supersede stale paused runs and always deliver the current result.

## 2026-08-15 — Queue-less canonical attempts

- Let the coach record and credit user-confirmed canonical solutions without requiring an open review or five-problem proposal.
- Keep approval replays idempotent while preserving canonical metadata and proposal eligibility safeguards.
