# #043 — Callback query handler infrastructure

**Milestone:** M9 phase-9 · **Labels:** `type:feature` `area:telegram` `area:webhooks` `prio:P0`
**Depends on:** #014
**Spec:** `plan/PHASE9_DESIGN.md` §Callback Handler Architecture

## Summary
Build the callback query handler that receives Telegram inline button taps,
parses `callback_data`, and dispatches to action handlers. Add the two new
Telegram API wrappers needed: `answer_callback_query` (toast notifications)
and `edit_message_reply_markup` (swap/remove buttons).

## Context
- The webhook (`webhooks/telegram.py`) already extracts `chat_id` from
  `callback_query` updates (line 53) and passes them to
  `flow_b.handle_update`. But `flow_b.handle_update` currently DROPS
  callback queries at line 156-162 ("callback_query or non-text update —
  out of scope for v1... Silent drop"). This issue adds the handler AND
  wires it in by modifying that early-drop.
- Telegram inline buttons carry a `callback_data` string (≤64 bytes). When
  tapped, Telegram sends an Update with `callback_query` containing the data.
- The bot must `answerCallbackQuery` within ~15 seconds or Telegram shows
  "loading" to the user. This is for the toast/notification, not the action.
- `editMessageReplyMarkup` swaps just the buttons without re-sending the
  message text. Used to add/remove the Cancel button, remove buttons after
  pick, etc.
- Callback data format: `action:slug` (e.g. `pick:two-sum`) or `action`
  (e.g. `cancel`). Slug is the universal key (decision #7).
- **callback_data ≤64 bytes constraint:** LeetCode slugs can be up to 200
  chars (per the `LeetCodeProblem.slug` model). `action:` + a 200-char slug
  far exceeds 64 bytes. Mitigation: validate slug length before building
  `callback_data`; if `len(action) + 1 + len(slug) > 64`, use a short hash
  (e.g. first 8 chars of MD5) and store a `callback_hash → slug` mapping in
  `bot_state` (TTL 7 days). Most LeetCode slugs are <55 chars so this is an
  edge case, but it must be handled or the Telegram send will fail. This
  issue builds the hash helper; #044-#046 use it.

## Tasks
- [ ] `integrations/telegram.py`: add `answer_callback_query(callback_query_id,
      text=None, show_alert=False)` — posts to `answerCallbackQuery`.
- [ ] `integrations/telegram.py`: add `edit_message_reply_markup(chat_id,
      message_id, reply_markup)` — posts to `editMessageReplyMarkup`.
      Pass `reply_markup=None` (or empty dict) to remove all buttons.
- [ ] `webhooks/callbacks.py` (new module):
  - `async def handle_callback_query(update: Update) -> bool`:
    - Extract `callback_query.data`, `callback_query.message.message_id`,
      `callback_query.from.id` (for logging), `callback_query.id`.
    - Parse `callback_data` into `(action, slug)` — split on first `:`.
      If the slug looks like a hash (8 hex chars), resolve it via the
      `callback_hash → slug` mapping in `bot_state`.
    - Always `answer_callback_query` first (even on error — prevents the
      Telegram "loading" spinner).
    - Dispatch to handler based on `action`. Unknown action → toast
      "Unknown action" + log warning.
    - Return True if handled, False if not a callback query.
  - Action handler registry: `dict[str, Callable]` mapping action → async
    function. This issue builds the registry + a `register_action(action,
    handler)` helper. **Each of #044–#047 must call `register_action` to
    wire its handlers** — registration is an explicit task in those issues,
    not implicit. This issue only builds the dispatch infrastructure + the
    registry + the registration helper.
  - `def register_action(action: str, handler: Callable) -> None`:
    Adds `handler` to the registry under `action`. Called at import time
    by #044–#047 (e.g. `register_action("pick", handle_pick)`). Raises
    `ValueError` on duplicate registration to catch wiring bugs early.
  - `def encode_callback_data(action: str, slug: str) -> str`:
    - If `len(action) + 1 + len(slug) <= 64` → return `f"{action}:{slug}"`.
    - Else → compute `hashlib.md5(slug.encode()).hexdigest()[:8]`, store
      `callback_hash:{hash}` → `slug` in `bot_state` (via `set_state`),
      return `f"{action}:{hash}"`. The handler resolves the hash back.
- [ ] `flows/flow_b.py`: modify `handle_update` (line 156-162) — BEFORE the
      `msg is None` drop, check `if update.callback_query is not None:`
      and dispatch to `webhooks.callbacks.handle_callback_query(update)`,
      then return. Remove the "callback_query... out of scope" comment.
      This is the wiring point — NOT `main.py` (the webhook route already
      passes callback queries to `handle_update`; the drop happens here).
- [ ] `tests/test_callbacks.py`:
  - Parse `pick:two-sum` → action=`pick`, slug=`two-sum`.
  - Parse `cancel` → action=`cancel`, slug=None.
  - `encode_callback_data` with short slug → `action:slug` directly.
  - `encode_callback_data` with long slug (200 chars) → `action:hash`,
    mapping stored in `bot_state`, handler resolves hash → slug.
  - Unknown action → toast + warning log.
  - `answer_callback_query` is always called (even on error).
  - Empty/malformed callback_data → toast "Invalid request" + log.

## Acceptance criteria
- [ ] `answer_callback_query` posts to the correct Telegram endpoint.
- [ ] `edit_message_reply_markup` posts to the correct Telegram endpoint.
- [ ] Callback query handler parses `action:slug` and `action` formats.
- [ ] `encode_callback_data` handles long slugs via hash mapping.
- [ ] `flow_b.handle_update` dispatches callback queries to the new handler
      (no longer drops them).
- [ ] Unknown actions produce a toast and a warning log, don't crash.
- [ ] `answer_callback_query` is always called (no hanging spinners).
- [ ] All existing webhook tests still pass.

## Principles
- **KISS:** the handler is a parser + dispatcher. No business logic here.
- **Resilience (NFR-1):** always answer the callback query, even on error.
  A hanging spinner is worse than an error toast.
- **Explicit over implicit:** the action registry is a dict, not magic
  string matching. Each action is a named function.
