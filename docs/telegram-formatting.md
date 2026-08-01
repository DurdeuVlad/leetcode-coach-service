# Telegram message formatting

**Status:** Reference + plan. The plan in §3 supersedes the Phase 9 issue stubs
where it conflicts; the Phase 9 design doc (`plan/PHASE9_DESIGN.md`) is the
product spec, this doc is the implementation contract for the formatting layer.

## 1. Why this doc exists

The propose message the user actually received looked like this:

```
1. *String to Integer (atoi)* — String — hard — https://leetcode\.com/problems/string\-to\-integer\-atoi/
   Why: Your recent log and active lessons are empty...
   Hint: With no active lesson recorded, explicitly define each parsing phase...
```

Two things are wrong with that picture:

1. **`*...*` is rendered as literal asterisks**, not italic. The user sees
   `*String to Integer (atoi)*` verbatim.
2. **`\.` and `\-` are rendered as literal backslashes.** The URL reads
   `https://leetcode\.com/problems/string\-to\-integer\-atoi/` — broken link,
   ugly, and clearly not what a coach UI should look like.

Both are the same bug: **the LLM was told to emit MarkdownV2, but the code
sends the string as plain text.** MarkdownV2 only takes effect when
`parse_mode="MarkdownV2"` is passed to `sendMessage`. Without it, Telegram
treats every character as literal — including the escape backslashes the LLM
sprinkled in because the prompt told it to.

This doc explains Telegram's formatting model (so the fix isn't cargo-culted),
records the current state of every message in the system, and specifies the
target format + the prompt/code changes to get there.

## 2. Telegram formatting modes — the cheat sheet

Telegram's Bot API `sendMessage` accepts an optional `parse_mode`:

| `parse_mode`   | What it does                                            | When to use                          |
|----------------|---------------------------------------------------------|--------------------------------------|
| (omitted)      | Plain text. Every character is literal. **No escaping needed, no formatting possible.** | System strings you fully control, error toasts, debug dumps. |
| `"MarkdownV2"` | Telegram's markdown dialect. Supports `*bold*`, `_italic_`, `__underline__`, `~strike~`, `[label](url)`, inline `` `code` ``, and ``` ```block``` ```. **Every one of these characters in normal text MUST be escaped with a backslash: `_ * [ ] ( ) ~ \` > # + - = | { } . !`** | Almost never the right choice for LLM-generated text. The escape set is huge and LLMs get it wrong constantly. |
| `"HTML"`       | Subset of HTML. Supports `<b>`, `<i>`, `<u>`, `<s>`, `<a href="...">`, `<code>`, `<pre>`, `<blockquote>`, `<tg-emoji>`. **Only `<`, `>`, `&` need escaping (via `html.escape`); everything else passes through.** | **The right choice for this project.** LLM output goes through `html.escape` once and is safe. |

### Why MarkdownV2 is the wrong default for LLM output

Telegram's MarkdownV2 escape set is **19 characters**: `_ * [ ] ( ) ~ \` > # + - = | { } . !`

Every period in a URL, every hyphen in a slug, every parenthesis in a problem
title ("String to Integer (atoi)") must be backslash-escaped. The LLM has to
get all 19 rules right, in every string, every time. Empirically it doesn't —
it either over-escapes (the bug we have now: `\.` and `\-` leaking into plain
text because the code doesn't set `parse_mode`) or under-escapes (Telegram
returns `400 Bad Request: can't parse entities` and the message is not sent at
all).

HTML has **3 escape characters** (`<`, `>`, `&`) handled by a single
`html.escape()` call in Python. The LLM never touches the escaping — it emits
plain text for content and a small fixed set of tags for structure. This is
why `flow_b.py` already uses `parse_mode="HTML"` for coach feedback and
per-problem threads, and why the Phase 9 design doc specifies HTML for the
propose card.

### The Telegram 4096 / 1024 limits

- `sendMessage` text limit: **4096 characters** after formatting is applied.
- `callback_data` limit: **64 bytes** (relevant for inline buttons, not
  formatting — see `plan/PHASE9_DESIGN.md` §"callback_data ≤64 bytes
  mitigation").
- Captions (media messages): **1024 characters**. Not used in this project.

The propose card with 5 problems + reasoning + hints runs ~1500–2500 chars.
No splitting needed. Coach feedback runs ~800–2000 chars. No splitting needed.

## 3. Plan — prompt + code changes for great-looking messages

The goal: every user-facing Telegram message is **HTML-mode, LLM content
passed through `html.escape`, structure via a small fixed tag set, sent with
`parse_mode="HTML"`**. No MarkdownV2 anywhere in the project.

### 3.1 The formatting contract (all messages)

Every message sent to the user follows the same pipeline:

```
LLM emits JSON with plain-text content fields
  → code extracts the fields
  → code html.escape()s every field that contains user/LLM free text
  → code assembles the final HTML using a fixed template (in code, NOT in the prompt)
  → send_message(..., parse_mode="HTML")
```

**The LLM never writes HTML.** The LLM emits plain-text fields (`title`,
`reasoning`, `coaching_hint`, `tutor_feedback`, etc.). The code wraps them in
`<b>`, `<i>`, `<a href="...">`, `<blockquote>` per a template defined in
Python. This is the single most important rule — it's what makes the output
reliable.

Allowed tag set (Telegram subset, no `<br>`, no `<p>`, no `<ul>`):
`<b>`, `<i>`, `<u>`, `<s>`, `<a href="...">`, `<code>`, `<pre>`,
`<blockquote>`, `<tg-emoji emoji-id="...">`.

### 3.2 Message-by-message changes

#### 3.2.1 Propose message (Flow A — the 5-candidate list)

**Current state (broken):**
- Prompt (`prompts/propose.py` line 60) asks the LLM for a
  `candidate_list_markdown` field that is a "numbered MarkdownV2 string" with
  `*Title*` italics and literal URLs.
- Code (`flow_a.py` line 259) sends that string with **no `parse_mode`** — so
  the MarkdownV2 escapes (`\.`, `\-`) and markup (`*`) show up as literal
  characters. This is the bug the user saw.

**Target (per `plan/PHASE9_DESIGN.md` §"Propose Message"):**

```html
<b>📊 Today's Problems</b>

<b>1. <a href="https://leetcode.com/problems/string-to-integer-atoi/">String to Integer (atoi)</a></b> 🔴 hard
<i>String</i>
<blockquote><b>Why:</b> {reasoning}
<b>Hint:</b> {coaching_hint}</blockquote>

<b>2. <a href="...">...</a></b> 🟡 medium
<i>...</i>
<blockquote>...</blockquote>

[... 3, 4, 5 ...]

<b>Credits: +3.5 (ahead 1 day)</b>
```

**Prompt change (`prompts/propose.py`):**
- Remove the `candidate_list_markdown` field from the JSON contract entirely.
  The LLM should not be formatting messages.
- Keep `candidates` as a JSON array with the 7 plain-text fields (`slug`,
  `title`, `url`, `tags`, `difficulty`, `reasoning`, `coaching_hint`).
- Update the prompt prose: replace the "Output a JSON object with exactly two
  fields" paragraph with "Output a JSON object with exactly one field:
  `candidates` — a JSON array of 5 objects...". Remove all mention of
  MarkdownV2 and `candidate_list_markdown`.
- This is a **prompt change**, so per `AGENTS.md` it must be accompanied by a
  `docs/business-requirements.md` note recording the decision (the
  `candidate_list_markdown` field is dropped because rendering belongs in code,
  not the LLM — the LLM is bad at MarkdownV2 escaping and the code was sending
  its output as plain text anyway).

**Code change (`flow_a.py`):**
- New function `_render_propose_html(candidates: Sequence[dict]) -> str` that
  builds the card-style HTML above. All LLM-derived strings (`title`, `tags`,
  `reasoning`, `coaching_hint`) pass through `html.escape`. URLs are
  interpolated into `<a href="{url}">` — URLs come from the validated
  `leetcode.com/problems/<slug>/` shape (already enforced by
  `_validate_candidates`), so they're safe to embed without escaping, but
  `html.escape` on the URL too is harmless and defensive.
- Difficulty badge: `hard` → 🔴, `medium` → 🟡, `easy` → 🟢. Map in code, not
  in the prompt.
- `flow_a.py` line 259 changes from `await send_message(target_chat, markdown)`
  to `await send_message(target_chat, html, parse_mode="HTML")`.
- `_parse_candidates` returns just `candidates` now (no `markdown`).
- Tests in `tests/test_flow_a.py` update: assert the rendered HTML contains
  `<b>📊 Today's Problems</b>`, the escaped title, the `<a href="...">` link,
  the difficulty emoji, and that no backslash-escapes appear anywhere. Add a
  regression test with a title containing `(`, `)`, `-`, `.` to prove
  `html.escape` handles them and no `\.`, `\-` leaks.

#### 3.2.2 Per-problem thread (Flow B — the "send your code" message)

**Current state (already HTML, already good):**
- `flow_b.py` lines 323–333 builds the message in code with `html.escape` on
  `c.title`, `c.difficulty`, `c.coaching_hint`, and sends with
  `parse_mode="HTML"`. This is the pattern to copy.

**Target:**
- Add the hyperlink and difficulty badge to match the propose card:

```html
<b>Problem 1/2: <a href="{url}">{escaped_title}</a></b> 🔴 {difficulty}

<blockquote>{escaped_coaching_hint}</blockquote>

Reply to this message with your code.
```

- Inline keyboard `[⏭️ Skip] [💡 Hint] [📖 Solution] [🤔 Why]` per Phase 9
  issue #045. (Out of scope for the formatting-only pass; tracked in #045.)
- The prompt (`prompts/coach.py`) does not need changes for this message —
  `coaching_hint` is already a plain-text field.

#### 3.2.3 Coach feedback (Flow B — the LLM review reply)

**Current state (already HTML, partially safe):**
- The coach prompt (`prompts/coach.py` line 58) tells the LLM to emit
  `tutor_feedback` as an **HTML-formatted** string with `<b>` tags for the
  lesson footer.
- `flow_b.py` line 691 sends it with `parse_mode="HTML"`.
- The footer (`flow_b.py` lines 726–734) is built in code with `html.escape`
  on `outcome.title` — good.
- **Bug:** the LLM's `tutor_feedback` body is **not** passed through
  `html.escape` before sending. If the LLM emits a literal `<` or `&` in its
  review (e.g. "your code uses `x < y` where..."), Telegram will either
  mis-parse it or reject the message with `400 can't parse entities`.

**Target:**
- **Prompt change (`prompts/coach.py`):** the LLM should emit `tutor_feedback`
  as **plain text**, not HTML. Remove the instruction to use `<b>` tags for
  the lesson footer — the code builds the footer (it already does, lines
  726–734). The prompt should say: "`tutor_feedback`: plain-text coaching
  feedback, all 5 sections. Do not include any HTML tags. Do not include the
  lesson footer — the system appends it."
- **Code change (`flow_b.py`):** `html.escape` the `tutor_feedback` body
  before joining with the footer. The footer is already escaped. The 5
  section labels ("1. Correctness:", "2. Complexity:", etc.) become `<b>`
  tags in code, not in the prompt. Final shape:

```html
<b>1. Correctness:</b> {escaped_section_body}

<b>2. Complexity:</b> {escaped_section_body}

...

<i>{footer}</i>
```

- This requires the LLM to emit the 5 sections in a predictable shape so the
  code can split on the section labels. **Alternative (simpler, recommended
  for v1):** keep `tutor_feedback` as a single plain-text blob, `html.escape`
  the whole thing, wrap in `<blockquote>` for visual grouping, append the
  footer. The 5 sections stay as plain-text numbered lines inside the
  blockquote. Less pretty, but no fragile splitting. The Phase 9 issue #046
  can prettify further if desired.

#### 3.2.4 Pinned progression message

**Current state:** plain text, no `parse_mode`. Built in
`flows/pinned.py` (not shown above, but per Phase 9 issue #048 it needs the
credits line).

**Target:** HTML, single block, no LLM content (all values are DB-derived
integers/floats/strings under our control). `html.escape` the lesson titles
(they're user-coach-generated). Shape per Phase 9 §"Pinned Message":

```html
📊 <b>Progress</b>
Credits: <b>+3.5</b> (ahead 1 day)
Open: 2 | Coached today: 1
📚 Active lessons: 3
🔥 Streak: 12 days
```

#### 3.2.5 Status / error / nudge / command replies

All currently plain text. Keep them plain text (no `parse_mode`) — they are
fully controlled strings with no LLM content and no formatting needs. The
exception is the nudge message (Phase 9 issue #047), which should be HTML for
the `<b>` deficit amount and the inline buttons. Tracked in #047.

### 3.3 The `html.escape` rule, stated once

**Every string that originated from an LLM output field, and every string that
originated from a DB column the LLM wrote to (lesson titles, coaching hints,
reasoning), passes through `html.escape` before being interpolated into an
HTML message.** No exceptions. Strings the code fully controls (status
labels, counts, URLs from the validated leetcode.com shape) may skip escaping
but escaping them too is harmless.

### 3.4 Test contract

For every message type, add a test that:
1. Feeds the renderer an LLM-shaped input containing every Telegram-HTML
   special character: `<`, `>`, `&`, `"`, `'`, plus the MarkdownV2 escape
   characters that were leaking: `.`, `-`, `(`, `)`, `!`, `*`.
2. Asserts the rendered HTML contains **no literal `\.`, `\-`, `\(`, `\)`**
   (the old MarkdownV2 leak).
3. Asserts the rendered HTML, when sent through a real Telegram HTML parser
   (or the `html.parser` stdlib as a proxy), parses without error.
4. Asserts the visible text (tags stripped) contains the original title
   unchanged — proving `html.escape` preserved the content.

## 4. Implementation order

This is a formatting-only slice of Phase 9. It does not depend on the credit
ledger (#040–#042) or the callback infrastructure (#043). It can land first
and immediately improve the UX while the bigger Phase 9 work continues.

1. **`prompts/propose.py`** — drop `candidate_list_markdown` from the JSON
   contract. Update the prompt prose. Update the contract comment block.
2. **`docs/business-requirements.md`** — add a one-paragraph decision note:
   "FR-1.x: the propose message is rendered in code from the `candidates`
   array, not by the LLM. The LLM no longer emits a markdown string. Reason:
   MarkdownV2 escaping is unreliable in LLM output and the previous code sent
   it as plain text, producing visible `\.`/`\-` artifacts."
3. **`flow_a.py`** — new `_render_propose_html`, update `_parse_candidates` to
   return only `candidates`, update the `send_message` call to
   `parse_mode="HTML"`.
4. **`prompts/coach.py`** — change `tutor_feedback` contract from
   "HTML-formatted" to "plain text, no HTML tags, no footer".
5. **`flow_b.py`** — `html.escape` the `tutor_feedback` body, wrap in
   `<blockquote>`, append the (already-escaped) footer. Update the per-problem
   thread to use the hyperlink + badge shape.
6. **`flows/pinned.py`** — switch to HTML, add `parse_mode="HTML"` to the
   `edit_message_text` / `send_message` calls.
7. **Tests** — update `test_flow_a.py`, `test_flow_b.py`, `test_pinned.py`
   per §3.4. Add the leak-regression test for the propose card.
8. **Manual verification** — once Cloudflare lets HTTPS through (the
   outstanding ops issue), trigger `/propose` via the admin endpoint and
   visually confirm the card renders with hyperlinks, badges, and no
   backslashes.

## 5. What NOT to do

- **Do not ask the LLM to emit HTML.** It will get the tag syntax wrong, mix
  in MarkdownV2 habits, and produce the same class of bug in a different
  dialect. The LLM emits plain-text fields; the code renders HTML.
- **Do not use MarkdownV2 anywhere.** The escape set is too large and the LLM
  cannot reliably hit it. HTML + `html.escape` is strictly simpler.
- **Do not skip `html.escape` on LLM fields because "the LLM was told not to
  use special characters."** The LLM will use them anyway (it's reviewing
  code — `<`, `>`, `&` are common in code). Escape once, on every field, no
  exceptions.
- **Do not split the coach feedback into 5 `<b>`-labelled sections by parsing
  the LLM's plain-text output in v1.** The split is fragile (the LLM doesn't
  always use the exact label text). Use the single-`<blockquote>` shape in
  §3.2.3. Prettify in #046 if needed.
- **Do not change the prompt prose style.** Per `AGENTS.md`, prompts are
  ported verbatim from n8n and changes must be recorded in
  `docs/business-requirements.md`. The changes in §3.2 are structural
  (removing the `candidate_list_markdown` field, changing `tutor_feedback`
  from HTML to plain text) — they are not prose rewrites. The coaching
  guidance prose ("Be honest about wrong answers — false praise costs
  interviews") stays untouched.
