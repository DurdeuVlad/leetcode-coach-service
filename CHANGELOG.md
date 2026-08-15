# Changelog

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
