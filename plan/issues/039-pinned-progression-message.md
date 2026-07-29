# #039 — Pinned progression message

**Milestone:** M8 phase-8c · **Labels:** `type:feature` `area:flow-b` `area:telegram` `prio:P1`
**Depends on:** #036, #009, #016, #022, #024
**Spec:** `docs/business-requirements.md` FR-8 (Pinned progression message)

## Summary
Maintain one pinned Telegram message with a compact snapshot of today's
status and the user's streak. Refreshed after each Flow A run, Flow B pick,
and Flow B coach pass. No new cron job.

## Context
- FR-8.1: snapshot = today's status counts (proposed/picked/coached/expired),
  active lessons count, current streak.
- FR-8.2: refresh trigger = after Flow A run, Flow B pick, Flow B coach
  pass. No new cron.
- FR-8.3: pinned message ID stored in `bot_state` (key `pinned_message_id`),
  not an env var. Depends on #036.
- FR-8.4: if `editMessageText` fails (message deleted, permissions changed),
  create a new pinned message and store the new ID.
- The snapshot is a strict subset of what `/status` (#038) shows — just the
  counts, not the full lesson list. Keep it short; it's pinned, not a feed.

## Tasks
- [ ] `integrations/telegram.py`: add `edit_message_text(chat_id,
      message_id, text)`, `pin_message(chat_id, message_id)`,
      `unpin_message(chat_id, message_id)`. Use the existing
      `python-telegram-bot` v21+ client + tenacity retry policy. Match the
      style of the existing `send_message` / `send_reply`.
- [ ] Snapshot builder in `flows/pinned.py` (or a helper):
  - Today's `daily_candidates` counts by status (proposed/picked/coached/
    expired — confirm the exact status values against the schema).
  - Active `tutor_lessons` count (`active = true`).
  - Current streak (same definition as #038 — extract to a shared helper,
    do not duplicate).
  - Format as a short markdown message (≤10 lines).
- [ ] Refresh hook `refresh_pinned_message()`:
  - Read `pinned_message_id` from `bot_state`. If missing, create + pin a
    new message, store the ID, return.
  - If present, `editMessageText` on that message with the new snapshot.
  - On `editMessageText` failure (Telegram returns 400 "message is not
    modified" is OK and should be a no-op; "message to edit not found" or
    "chat not found" → recovery path): unpin the old (best-effort), create
    + pin a new message, store the new ID in `bot_state`.
- [ ] Wire the refresh hook into:
  - `flow_a.propose_5` (after the proposal message is sent).
  - `flow_b._pick_parse_path` (after the per-pick threads are created).
  - `flow_b._post_coach_updates` (after the coach pass completes).
  - The admin API endpoints in `webhooks/admin.py` (after their respective
    flows complete) — so the pinned message also updates when the external
    tester drives the pipeline.
- [ ] The "message is not modified" case (Telegram 400 with that exact
      message) is a no-op, not an error. Do not log it as a failure.

## Acceptance criteria
- [ ] After `flow_a.propose_5()` runs, the pinned message shows
      `proposed: 5` (or the day's count).
- [ ] After a pick, the pinned message shows `picked: N`.
- [ ] After a coach pass, the pinned message shows `coached: N` and the
      updated streak.
- [ ] If `pinned_message_id` is unset in `bot_state`, the first refresh
      creates + pins a new message and stores the ID.
- [ ] If the pinned message was deleted manually, the next refresh creates
      + pins a new message and updates `bot_state`.
- [ ] "message is not modified" Telegram response → no-op, no error log.
- [ ] The refresh hook is also called from the admin API endpoints (so the
      external tester sees the pinned message update).
- [ ] Covered by `tests/test_pinned.py`.

## Principles (how they apply here)
See [#034](034-engineering-principles-layers.md).
- **KISS:** the snapshot is a few counts + a streak. Not a dashboard. The
  pinned message is text, not a custom keyboard or inline buttons.
- **DRY:** the streak calculation is shared with #038's `/status`. Extract
  to one helper, do not copy-paste.
- **Resilience (NFR-1):** the recovery path (edit fails → create new) means
  a deleted pinned message is self-healing, not a manual ops task.
- **Explicit over implicit:** the "message is not modified" case is
  explicitly a no-op, not a swallowed error. Distinguishing it from a real
  edit failure requires checking the Telegram error description string —
  do that explicitly, do not blanket-swallow 400s.

## Notes
- The refresh hook is fire-and-forget from the flow's perspective: a
  failure to update the pinned message must not fail the flow itself. Wrap
  the hook call in a try/except that logs and continues.
- Do not add a cron job for this. FR-8.2 is explicit: refresh on event, not
  on schedule. A cron would be scope creep against the spec.
- The admin API integration (calling the hook from `webhooks/admin.py`) is
  in this issue's scope because the admin API is the test surface for the
  whole pipeline — if the pinned message doesn't update under admin-driven
  runs, the external tester can't verify it.
