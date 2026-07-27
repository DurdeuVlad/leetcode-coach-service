# #021 — Reply correlation / routing

**Milestone:** M3 flow-b · **Labels:** `type:feature` `area:flow-b` `risk:high` `prio:P0`
**Depends on:** #003, #019, #020

## Summary
The data-driven router that decides whether an incoming message is a pick-list
reply or a coach-pass submission — and never guesses.

## Context
- `docs/business-requirements.md` FR-2.2 is the exact algorithm:
  1. If `reply_to_message.message_id` present → look up `pending_review` by
     that `message_id`. **Found** → coach pass. **Not found** → pick-parse
     (it was a reply to the 5-list, whose id isn't in `pending_review`).
  2. If no `reply_to_message` → fuzzy-match text against **today's open**
     `pending_review` rows by problem title. Exactly one match → coach pass.
     Zero or multiple → send clarification ("Which one — 1) X 2) Y?") and stop.
     **Never guess.**

## Tasks
- [ ] `flows/flow_b.py` `handle_update()` correlation logic implementing FR-2.2
      precisely.
- [ ] Fuzzy title matcher over today's `status = open` rows.
- [ ] Clarification-prompt-and-stop branch for 0/multiple matches.
- [ ] Route to pick-parse (#022) or coach pass (#024) accordingly.

## Acceptance criteria
- [ ] Reply-to a per-problem message (id in `pending_review`) → coach path.
- [ ] Reply-to the 5-list (id absent) → pick-parse path.
- [ ] No reply-to + exactly one fuzzy match → coach path.
- [ ] No reply-to + zero/multiple matches → clarification sent, no state change.
- [ ] Never auto-selects on ambiguity (asserted in #027).

## Principles (how they apply here)
See [#034](034-engineering-principles-layers.md). KISS is the tie-breaker.
- **Explicit over implicit / fail safe:** routing is data-driven
  (`message_id`, exact match count). On ambiguity it asks — it **never guesses**
  (FR-2.2).
- **KISS:** implement FR-2.2 literally; no NLP intent classifier, no
  probabilistic scoring beyond the specified fuzzy title match.
- **Single Responsibility:** this function *routes only* — it decides the path
  and delegates to pick-parse (#022) or coach (#024); it does no side effects.

## Notes
- Routing is on data (ids, matches), never on parsing free-text intent.
