# Telegram constraints for a low-friction coaching experience

**Scope.** Official Telegram Bot API and Bot Features documentation, checked
2026-08-05. This is a design constraint note for one interruptible Coaching
Session with natural-language input, not a proposal for a multi-turn agent.

## Hard platform constraints

| Area | Constraint | Consequence |
|---|---|---|
| Text and formatting | `sendMessage` and `editMessageText` accept 1-4096 characters **after entity parsing**. Media/document captions are 0-1024 after parsing. HTML, Markdown, or explicit entities are supported; entity nesting has restrictions. | Render and count final visible text before sending. Keep code and detailed feedback as normal text/document, not a caption. Escape untrusted text when using HTML. |
| Code and documents | Bots can upload a document of any type up to 50 MB; multipart upload is the practical path. Files sent by URL are limited to 20 MB for non-photo content, and downloading with `getFile` is limited to 20 MB. | Accept ordinary pasted code first. Accept a code file as `document`, download only after enforcing the 20 MB inbound limit, then apply an application-level source-size/type limit. Preserve the original `file_id`/metadata for audit; do not treat filenames or MIME type as trustworthy. |
| Replies | `reply_parameters.message_id` anchors an outgoing reply. `allow_sending_without_reply=true` is available when the target may have disappeared. Quotes must be exact substrings or Telegram rejects the send. | Store `chat_id` and bot `message_id` for the active session. Reply to the submitted message for feedback; use `allow_sending_without_reply` for non-critical confirmations, not the main coaching result. Do not build the UX around fragile quoted snippets. |
| Buttons | An inline button uses exactly one action type. `callback_data` is **1-64 bytes**, not characters. The callback carries its own query id and may expose an inaccessible origin message. Clients keep a progress indicator until `answerCallbackQuery` is called. | Put only a short opaque action/version/token in callback data, e.g. `cs:<session-id>:hint:v2`; persist authoritative state server-side. Acknowledge immediately, then perform work. Treat every callback as stale/replayable and validate user, chat, session state, action, and version. |
| Editing | Bot messages can be edited by `chat_id` + `message_id`; edits are intended to reduce inline-keyboard clutter. Telegram currently permits edits only for messages without markup or with an inline keyboard. | Use one editable session card: `Attempting` -> `Awaiting code` -> `Coached`/`Skipped`. Disable or replace its keyboard after a state transition. If edit fails because the message is unavailable, send a new card and update the stored pointer. |
| Webhooks | Telegram posts JSON updates to the HTTPS webhook and retries any non-2xx response, eventually giving up. `update_id` is unique and normally sequential; Telegram says it can be used to ignore repeats or recover ordering. Updates are retained no longer than 24 hours. `secret_token` yields the `X-Telegram-Bot-Api-Secret-Token` header. | Verify the secret header before parsing. Persist `update_id` with a unique constraint **before side effects**, make all session transitions idempotent, and return 2xx only after durable acceptance. Do not rely on delivery order. Configure `allowed_updates` narrowly (`message`, `callback_query`, optionally `edited_message`); observe `getWebhookInfo` for delivery failures. |
| Notifications | `disable_notification=true` sends silently (the user still receives a notification without sound). | Use normal notifications for an explicit coaching result or action required now; make nudges, card edits, duplicate-safe acknowledgements, and background progress silent. Do not emit a notification for every state change. |
| Commands | Command names are 1-32 lowercase ASCII letters/digits/underscores; descriptions are 1-256 characters; at most 100 can be registered. Client menus suggest registered commands, but incoming commands are not proof of registration or authorization. | Register a small discoverable set (`/start`, `/help`, `/status`, `/cancel`, perhaps `/coach`), but validate every command and chat/user server-side. Commands must be an escape hatch, not the main session protocol. |
| Privacy | Private-chat messages always reach the bot. In groups, privacy mode normally limits delivery to relevant commands, bot replies, inline messages, and replies to the bot; ForceReply is explicitly supported for step-by-step input without disabling privacy mode. | Prefer a private chat and an explicit chat/user allowlist. If a group is ever supported, retain privacy mode and use replies/ForceReply; never disable privacy merely to capture free-form code. |

## Recommended session model

Telegram does not provide a durable conversational-session primitive. Model it
in the database, not in reply chains or callback payloads:

1. Create one `coaching_session` with an opaque id, allowlisted `chat_id` and
   `user_id`, target problem, state, version, expiry, and current prompt
   `message_id`.
2. Send one concise problem card with inline actions: **Send code**, **Hint**,
   **Skip**, **Cancel**. `Send code` should make the input intent obvious;
   optionally use `ForceReply` for group-compatible flow, but natural text
   and document uploads must remain accepted at any time.
3. Route a natural-language message or code document to the session only when
   it replies to the card, names a unique open problem, or the user has one
   open session. If ambiguous, ask a short disambiguation question; never
   guess. Slash commands and buttons can interrupt at every state.
4. On a button tap, answer the callback first; atomically compare-and-swap the
   session version/state; then edit the card or send the relevant response.
   Old cards must return a short, idempotent “this session is already …”
   acknowledgement, not reopen work.
5. On submission, atomically claim the session (`awaiting_submission` ->
   `coaching`) before the LLM call. A duplicate webhook/update sees the claim
   and returns 2xx without starting another coach pass. Persist the final
   result before sending it, so a failed outbound send can be safely retried.

This preserves natural language while making interruption explicit: state is
authoritative, reply context is a convenience, and callbacks are small,
untrusted intents rather than state transport.

## Product calls and non-goals

- Inline keyboards are better than a persistent reply keyboard for
  per-problem actions: taps do not inject noise into the chat, and Telegram
  specifically recommends editing the keyboard after state changes.
- Keep the full coach response under 4,096 rendered characters. If it does
  not fit, send a compact verdict and attach the full review as a `.txt` or
  source document; do not silently truncate feedback.
- `protect_content` can discourage forwarding/saving but is not a security
  boundary. Treat Telegram-delivered code and feedback as user-visible data.
- A webhook endpoint is not authenticated by URL secrecy. Use `secret_token`,
  then independently enforce the allowlisted chat and user on every update.
- Telegram documents solve transport, not sandboxing. Never execute uploaded
  code in the webhook process; parsing and any later execution need separate
  resource and security controls.

## Source material

- [Telegram Bot API: sendMessage, formatting, message limits and notification controls](https://core.telegram.org/bots/api#sendmessage)
- [Telegram Bot API: updates, webhooks, retries, secret token and update IDs](https://core.telegram.org/bots/api#setwebhook)
- [Telegram Bot API: inline keyboards and callback queries](https://core.telegram.org/bots/api#inlinekeyboardbutton)
- [Telegram Bot API: reply parameters](https://core.telegram.org/bots/api#replyparameters)
- [Telegram Bot API: message editing](https://core.telegram.org/bots/api#updating-messages)
- [Telegram Bot API: sending and downloading files](https://core.telegram.org/bots/api#sending-files)
- [Telegram Bot API: commands](https://core.telegram.org/bots/api#botcommand)
- [Telegram Bot Features: commands, keyboards, and privacy mode](https://core.telegram.org/bots/features)

## Unresolved risks

- Telegram documents the retry condition but not a public retry schedule or
  delivery ordering guarantee; idempotency cannot be relaxed.
- The platform cannot prove that a pasted message semantically belongs to a
  session. The proposed routing policy needs product calibration after real
  use, especially when more than one session is open.
- Document upload introduces content-handling risk; the app still needs an
  explicit retention policy and a parser/resource-limit design before it is
  enabled.
