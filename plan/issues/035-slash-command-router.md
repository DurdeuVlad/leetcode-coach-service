# #035 — Slash command router

**Milestone:** M8 phase-8a · **Labels:** `type:feature` `area:flow-b` `prio:P0`
**Depends on:** #019, #021, #022, #024, #026, #016
**Spec:** `docs/business-requirements.md` FR-6 (Slash commands)

## Summary
Parse `/`-prefixed Telegram messages as commands **before** FR-2.2 reply
correlation runs, and dispatch to the matching flow internal. This is the
text-driven exception to FR-2.1's data-driven routing rule.

## Context
- FR-2.1 (amended 2026-07-29): slash commands are parsed before data-driven
  routing.
- FR-6.1: any message starting with `/` is a command.
- FR-6.6: unknown command → short reply, no LLM, no DB write.
- FR-6.5: commands only work from the allowlisted chat ID (NFR-4). No new
  auth surface — reuse the existing chat-id allowlist check.
- The admin API (merged on dev, 2026-07-29) already proved the `dry_run`
  plumbing. Commands call the same internals with `dry_run=False`.

## Tasks
- [ ] In `flow_b.handle_update` (or a new `flows/commands.py`), check if the
      inbound text starts with `/` **before** FR-2.2 reply correlation.
- [ ] Parse the command name (first whitespace-delimited token) and the
      remainder as args.
- [ ] Dispatch table:
  - `/propose` → `flow_a.propose_5(dry_run=False)`.
  - `/pick` → parse args as ≤2 ints, call `flow_b._pick_parse_path`.
  - `/coach` → parse args, call `flow_b._coach_pass_path` +
    `_post_coach_updates`. (See #037 for the target-resolution rules.)
  - `/status` → #038.
  - `/why` → #038.
  - unknown → short "unknown command" reply, return.
- [ ] All command paths enforce the chat-id allowlist (NFR-4). Non-allowlisted
      chat → silent drop, same as today's webhook.
- [ ] Unknown command path: no LLM call, no DB write, no Telegram send beyond
      the short error reply.

## Acceptance criteria
- [ ] `/propose` from the allowlisted chat triggers Flow A end-to-end (5
      candidates persisted + Telegram message sent).
- [ ] `/pick 1 2` triggers the pick-parse path with indices [1, 2].
- [ ] Unknown command `/foo` → "unknown command" reply, zero side effects.
- [ ] A non-`/` message still goes through FR-2.2 reply correlation
      (regression: existing Flow B tests still pass).
- [ ] Non-allowlisted chat → no reply, no side effects.
- [ ] Covered by a new `tests/test_commands.py`.

## Principles (how they apply here)
See [#034](034-engineering-principles-layers.md).
- **KISS:** a dispatch dict keyed by command name. No framework, no
  reflection.
- **Single Responsibility / layer:** the router only parses + dispatches;
  the actual work happens in the flow internals, same as the admin API.
- **Explicit over implicit:** the `/` check runs first and returns; it does
  not fall through to FR-2.2 if the command is recognized.

## Notes
- This issue does **not** implement `/coach` target resolution or the
  `/status` / `/why` handlers — those are #037 and #038. It builds the router
  and the `/propose` + `/pick` paths (which are thin wrappers over existing
  internals).
- The router is the only place that knows about commands. Flow internals
  stay command-agnostic.
