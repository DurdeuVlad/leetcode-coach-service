# LLM API Reference

Reference for the two LLM providers used by `src/leetcode_coach/integrations/llm.py`:
OpenAI (primary) and Google Gemini (fallback). Covers the exact parameters
the code passes, why, and the gotchas that bit us in production.

## Models

| Role     | Model ID          | Provider | Notes                                    |
|----------|-------------------|----------|------------------------------------------|
| Primary  | `gpt-5.6-sol`     | OpenAI   | GPT-5 series — reasoning model           |
| Fallback | `gemini-3.6-flash`| Google   | Gemini 3.6 Flash — fast, cheap fallback  |

Both are called via their official Python SDKs (`openai` and
`google-genai`). The `LLMClient` class in `llm.py` is the single entry
point — flows never call the SDKs directly.

---

## OpenAI Chat Completions API

**Endpoint:** `POST https://api.openai.com/v1/chat/completions`
**SDK:** `openai.AsyncOpenAI` → `client.chat.completions.create(...)`
**Docs:** https://developers.openai.com/api/docs/guides/structured-outputs

### Parameters used by this project

```python
resp = await client.chat.completions.create(
    model="gpt-5.6-sol",
    messages=[
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ],
    max_completion_tokens=2000,           # NOT max_tokens
    response_format={"type": "json_object"},
)
```

| Parameter              | Type | Required | Notes |
|------------------------|------|----------|-------|
| `model`                | str  | yes      | `gpt-5.6-sol` (GPT-5 series) |
| `messages`             | list | yes      | System + user message pair |
| `max_completion_tokens`| int  | no       | **Required for GPT-5/o-series.** Replaces deprecated `max_tokens`. Includes reasoning tokens (not visible in output but billed). |
| `response_format`      | dict | no       | `{"type": "json_object"}` enables JSON mode — guarantees valid JSON output. Supported on GPT-5 series. |

### `max_tokens` vs `max_completion_tokens` — the gotcha

**`max_tokens` is deprecated and not compatible with o-series or GPT-5
series models.** Using it with `gpt-5.6-sol` returns:

```
openai.BadRequestError: Error code: 400 - {'error': {'message':
"Unsupported parameter: 'max_tokens' is not supported with this model.
Use 'max_completion_tokens' instead."}}
```

**Why the rename:** o-series and GPT-5 models generate internal "reasoning
tokens" that are billed but not returned in the response. `max_tokens`
previously meant both "tokens generated" and "tokens you see back" — with
reasoning models those are no longer the same number. OpenAI introduced
`max_completion_tokens` as an explicit opt-in to the new semantics.

**Rule:** Always use `max_completion_tokens`. It works on all current
models (GPT-4o, GPT-5, o-series). `max_tokens` only works on legacy
models (GPT-3.5, GPT-4 non-reasoning) and is deprecated.

### JSON mode

`response_format={"type": "json_object"}` guarantees the output is
syntactically valid JSON. It does **not** enforce a schema — the caller
must validate the shape downstream (we do this in
`llm.parse_json_response`).

For schema enforcement, use `response_format={"type": "json_schema",
"json_schema": {"strict": true, "schema": ...}}` (Structured Outputs).
This project uses JSON mode, not Structured Outputs, because the prompt
already specifies the exact JSON shape and the flows validate it.

### Response shape

```python
resp.choices[0].message.content  # str — the completion text
resp.model                       # str — actual model used
resp.usage.prompt_tokens         # int — input tokens
resp.usage.completion_tokens     # int — output tokens (visible only)
```

### Exception classification (our decision table)

| SDK exception                  | Our class            | Action              |
|--------------------------------|----------------------|---------------------|
| `AuthenticationError` (401)    | `_AuthLLMError`      | No retry, fallback  |
| `APIConnectionError` / timeout | `_TransientLLMError` | Retry, then fallback|
| `RateLimitError` (429)         | `_TransientLLMError` | Retry, then fallback|
| `InternalServerError` (5xx)    | `_TransientLLMError` | Retry, then fallback|
| `BadRequestError` (400)        | `LLMUnavailableError`| No retry, no fallback |
| `PermissionDeniedError` (403)  | `LLMUnavailableError`| No retry, no fallback |
| `NotFoundError` (404)          | `LLMUnavailableError`| No retry, no fallback |

---

## Google Gemini API (generateContent)

**Endpoint:** `POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent`
**SDK:** `google.genai.Client` → `client.aio.models.generate_content(...)`
**Docs:** https://ai.google.dev/api/generate-content

### Parameters used by this project

```python
resp = await client.aio.models.generate_content(
    model="gemini-3.6-flash",
    contents=user,
    config=types.GenerateContentConfig(
        system_instruction=system,
        max_output_tokens=2000,
        response_mime_type="application/json",
    ),
)
```

| Parameter                    | Type   | Required | Notes |
|------------------------------|--------|----------|-------|
| `model`                      | str    | yes      | `gemini-3.6-flash` |
| `contents`                   | str    | yes      | The user prompt (system goes in config) |
| `config.system_instruction`  | str    | no       | System prompt — Gemini puts this in config, not messages |
| `config.max_output_tokens`   | int    | no       | Max output tokens. **Gemini's equivalent of OpenAI's `max_completion_tokens`.** |
| `config.response_mime_type`  | str    | no       | `"application/json"` enables JSON mode |

### Key differences from OpenAI

1. **System prompt location:** Gemini takes it in `config.system_instruction`,
   not as a `{"role": "system"}` message. The SDK does not accept a
   messages list — only `contents` (the user prompt).
2. **Token limit parameter:** `max_output_tokens` (in config), not
   `max_tokens` or `max_completion_tokens`.
3. **JSON mode:** `response_mime_type="application/json"` in config, not
   `response_format` in the top-level call.
4. **Async path:** `client.aio.models.generate_content(...)` — the `aio`
   namespace is the async entry point.

### Response shape

```python
resp.text                          # str — the completion text
resp.usage_metadata.prompt_token_count      # int — input tokens
resp.usage_metadata.candidates_token_count  # int — output tokens
```

### Exception classification

| SDK exception              | Condition    | Our class            | Action              |
|----------------------------|--------------|----------------------|---------------------|
| `genai_errors.ServerError` | any          | `_TransientLLMError` | Retry, then raise   |
| `genai_errors.ClientError` | code == 429  | `_TransientLLMError` | Retry, then raise   |
| `genai_errors.ClientError` | other code   | `LLMUnavailableError`| No retry, no fallback |

### Gotcha: max_output_tokens with structured output

If `max_output_tokens` is too low and the model hits the limit mid-JSON,
Gemini returns `None` for `resp.text` (finish_reason=`MAX_TOKENS`). The
caller gets an empty string, not a partial JSON. Our default of 2000 is
safe for the propose and coach prompts; if prompts grow, raise this.

---

## Mock mode

When `OPENAI_API_KEY` is `mock` or empty, `LLMClient.complete()` returns
canned responses without any network call. This lets flows run end-to-end
in tests and preview deploys without real API keys. The mock detects
propose vs coach by inspecting the system prompt content.

## Cost tracking

Every call emits a structured log with `tokens_in`, `tokens_out`, and
`model`. This feeds the NFR-2 cost budget (<$10/month). The `LLMResponse`
dataclass carries these fields from the SDK response to the caller.

---

## Admin API (automated end-to-end testing)

The admin API is an optional HTTP surface for an external AI or CI script
to drive the full Flow A → Flow B pipeline without Telegram. It is mounted
only when `ADMIN_API_KEY` is non-empty; with the env var blank the router
is not registered and the endpoints return 404. All admin endpoints call
the flow internals with `dry_run=True`, so Telegram sends are skipped but
DB writes and LLM calls still happen — the test proves the real pipeline
works end-to-end.

**Base URL:** `http://<host>:8000`
**Auth:** `X-Admin-Api-Key: <ADMIN_API_KEY>` header on every request.
Missing or mismatched key → `401 Unauthorized`.

### `POST /admin/propose`

Triggers `flow_a.propose_5(dry_run=True)`: pulls the unsolved pool, asks
the LLM for 5 candidates, and persists them as `daily_candidates` rows.
No Telegram message is sent.

**Request body:** none.

**Response 200:**
```json
{
  "markdown": "...the formatted proposal text...",
  "candidates": [
    {
      "pick_index": 1,
      "slug": "two-sum",
      "title": "Two Sum",
      "difficulty": "Easy",
      "reasoning": "...",
      "coaching_hint": "..."
    },
    ...
  ]
}
```

### `POST /admin/pick`

Triggers `flow_b._pick_parse_path(dry_run=True)`: for each requested
pick index, creates a Google Task and a `pending_review` row. Returns
the created threads including `pending_review_id` for the subsequent
`/admin/coach` call. No Telegram message is sent.

**Request body:**
```json
{ "picks": [1, 2] }
```

`picks` is a list of 1-based indices into the 5-candidate list produced
by `/admin/propose`. The handler joins them into the same `"1 2"` text
string the Telegram pick path produces.

**Response 200:**
```json
{
  "picked": [
    {
      "pick_index": 1,
      "problem_slug": "two-sum",
      "problem_title": "Two Sum",
      "difficulty": "Easy",
      "message_id": 0,
      "task_id": "...",
      "pending_review_id": 42
    },
    ...
  ]
}
```

`message_id` is `0` under `dry_run=True` (no Telegram message was sent).

### `POST /admin/coach`

Triggers `flow_b._coach_pass_path(dry_run=True)` for a submission on a
`pending_review` row: calls the coach LLM, runs the lesson decision and
post-coach updates (DB writes + Google Task update), and returns the
full coach result + lesson outcome as JSON. No Telegram message is sent.

**Request body:**
```json
{
  "code": "...the user's submission text or a status note like 'skipped'...",
  "pending_review_id": 42,
  "problem_slug": "two-sum"
}
```

Either `pending_review_id` or `problem_slug` identifies the target
`pending_review` row. If both are given, `pending_review_id` wins. When
`problem_slug` is used, the handler looks for an `open` row with that
slug proposed today.

**Response 200:**
```json
{
  "tutor_feedback": "...",
  "lesson_title": "...",
  "lesson_category": "...",
  "lesson_is_recurring": false,
  "lesson_should_graduate": false,
  "solved": false,
  "status": "coached",
  "next_step": "...",
  "time_spent_min": null,
  "lesson_action": "reinforce",
  "lesson_title_outcome": "...",
  "times_reinforced": 2,
  "reply_text": "..."
}
```

`lesson_should_graduate` is the LLM's recommendation; graduation is
double-gated — the DB row's `times_reinforced >= 5` is also required
(see AGENTS.md gotcha #4). `times_reinforced` in the response is the
post-coach DB count.

**Response 404:** `{ "detail": "No open pending_review row found. Run
/admin/propose then /admin/pick first, or provide a valid
pending_review_id." }` if no matching row exists.

### Typical automated test sequence

```
POST /admin/propose   → 200, 5 candidates persisted
POST /admin/pick      → 200, N pending_review rows created
POST /admin/coach     → 200, coach feedback persisted, lesson tracked
```

Each call is independent against the DB state, so the external tester
can run them in order or retry a failed step. The `dry_run=True` flag is
the only difference from the production path — the LLM prompts, DB
writes, and Google Tasks calls are identical to what cron + Telegram
would trigger.
