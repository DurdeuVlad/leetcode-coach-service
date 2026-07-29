# #037 — `/coach` command + target resolution

**Milestone:** M8 phase-8a · **Labels:** `type:feature` `area:flow-b` `prio:P0`
**Depends on:** #024, #026, #035
**Spec:** `docs/business-requirements.md` FR-6.4

## Summary
Implement the `/coach` command: trigger Flow B's coach pass from a slash
command. The non-trivial part is target resolution — when >1 `pending_review`
is open today, the user must specify which one.

## Context
- FR-6.4: `/coach <text>` triggers the coach pass. If >1 `pending_review` is
  open today, requires a target: `/coach <slug> <text>` or a reply-to. No
  target → short error, no LLM call.
- The coach pass internals (`flow_b._coach_pass_path` +
  `_post_coach_updates`) already exist and are `dry_run`-capable. This issue
  is about resolving the target `pending_review` row and calling them with
  `dry_run=False`.
- FR-7.3 does not apply here (that's progression queries); `/coach` is a
  write path.

## Tasks
- [ ] Target resolution order (in `flows/commands.py` or a helper):
  1. If the inbound message has `reply_to_message.message_id`, look up
     `pending_review` by that `message_id` (same as FR-2.2.1). The text after
     `/coach` is the submission.
  2. Else, parse `/coach <slug> <text>`: if the first token after `/coach`
     matches a `problem_slug` of an open `pending_review` today, use that
     row. The rest is the submission.
  3. Else, parse `/coach <text>` (no slug): if exactly one open
     `pending_review` today, use it. If >1, reply with the list of open
     problems and stop. If 0, reply "no open problems today" and stop.
- [ ] Call `flow_b._coach_pass_path(chat_id, inbound_message_id, review,
      user_text=submission, dry_run=False)` then `_post_coach_updates`.
- [ ] The 0-target and >1-target cases: short reply, no LLM call, no DB
      write.
- [ ] After a successful coach, trigger the pinned-message refresh hook
      (#039) — but only wire the call; #039 implements the hook itself.

## Acceptance criteria
- [ ] `/coach def foo(): pass` with exactly one open `pending_review` →
      coach pass runs, `pending_review.status = done`, `leetcode_log` row
      inserted.
- [ ] `/coach two-sum def foo(): pass` with two open reviews (one for
      `two-sum`, one for `three-sum`) → coaches the `two-sum` row.
- [ ] `/coach def foo(): pass` with two open reviews and no slug → reply
      lists both open problems, no LLM call.
- [ ] `/coach anything` with zero open reviews → "no open problems today",
      no LLM call.
- [ ] Reply-to a problem message with `/coach def foo(): pass` → coaches
      the review whose `message_id` matches the replied-to message.
- [ ] Covered by `tests/test_commands.py` (extend the file from #035).

## Principles (how they apply here)
See [#034](034-engineering-principles-layers.md).
- **KISS:** target resolution is a 3-step waterfall, each step cheap. No
  fuzzy matching, no LLM in the resolver.
- **Explicit over implicit:** ambiguous target → list the options and stop.
  Never guess (mirrors FR-2.2.2's "Never guess" rule).
- **DRY:** the actual coach work is the existing `_coach_pass_path` +
  `_post_coach_updates`. This issue only adds the target resolver and the
  command glue.

## Notes
- The slug-vs-text ambiguity: if the first token after `/coach` is a valid
  slug of an open review, treat it as the slug. Otherwise treat the whole
  remainder as the submission text and fall through to the single-open-review
  case. This is the only ambiguity; document it in the command's help text.
- This issue depends on #035 (the router) but can be developed in parallel
  once the router's dispatch shape is agreed.
