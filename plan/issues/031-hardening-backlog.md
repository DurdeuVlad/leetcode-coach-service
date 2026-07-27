# #031 — Post-v1 hardening backlog

**Milestone:** M7 hardening · **Labels:** `type:infra` `type:test` `prio:P1`
**Depends on:** #030

## Summary
Open-ended, data-driven hardening after v1 is live. These items are
**intentionally deferred** and several depend on real runtime data — do not
resolve the open decisions without it.

## Context
- `docs/roadmap.md` Phase 7 + `docs/business-requirements.md` §8 (open
  decisions). AGENTS.md: do not pick values just to "close the loop."

## Backlog (each can become its own issue when triggered)
- [ ] **Calibrate lesson graduation threshold** (open decision §8.1) after 2-3
      weeks of real data. The threshold is a single constant from #025.
- [ ] **Golden-output suite for the coach pass:** collect ~10 real coach
      responses, manually rate them, lock as regression baselines (extends
      #027).
- [ ] **SearXNG fallback for YouTube** — only if the YouTube Data API quota
      actually becomes a problem (§8.3; unlikely at ~1 search/day).
- [ ] **Browserless fallback for LeetCode GraphQL** — only if the endpoint
      actually rate-limits/blocks the homelab IP (§8.4). Replaces the #012 stub.
- [ ] **Lesson wording calibration** (§8.2) after seeing 5-10 real lessons.
- [ ] **Reconsider Google Tasks integration** (§8.5) if it causes more ops pain
      than value.
- [ ] **Observability upgrade:** structlog → Loki → Grafana dashboard if
      single-user logs stop being enough (architecture §11).

## Acceptance criteria
- [ ] Each item is only actioned when its **trigger condition** is met.
- [ ] Any resolved open decision is written back into
      `docs/business-requirements.md` §8 with the data that justified it.

## Notes
- Out-of-scope items (multi-user, web UI, photo evidence, Anki export, mock
  interviews) stay out of scope — see business-requirements §7 / architecture
  §12.

## Principles (how they apply here)
See [#034](034-engineering-principles-layers.md). KISS is the tie-breaker.
- **YAGNI is the whole point of this issue:** every item is **intentionally
  deferred** until its real-world trigger fires. Do not "preemptively
  build" SearXNG/Browserless/golden suites — that is exactly the
  future-proofing #034 forbids.
- **Explicit over implicit:** any open decision resolved here is written
  back into `docs/business-requirements.md` §8 **with the data that
  justified it** — never a silent code change.
- **KISS:** each item, when triggered, becomes its own focused issue — no
  "hardening mega-PR." The graduation threshold stays a single constant
  (set in #025) so recalibration is a one-line change.
- **Out-of-scope stays out-of-scope:** §7/§12 items (multi-user, web UI,
  Anki, mock interviews) are not backlog — they are *no*.
