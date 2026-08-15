# Changelog

## 2026-08-15 — Direct coaching actions

- Resolve exact LeetCode slugs on demand so valid solved work can be recorded even when the bounded local registry misses it.
- Execute explicit coaching actions without approval pauses while keeping canonical validation and retry-safe mutations.
- Let new instructions supersede stale paused runs and always deliver the current result.

## 2026-08-15 — Queue-less canonical attempts

- Let the coach record and credit user-confirmed canonical solutions without requiring an open review or five-problem proposal.
- Keep approval replays idempotent while preserving canonical metadata and proposal eligibility safeguards.
