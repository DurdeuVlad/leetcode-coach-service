# #010 — LLM client (primary + fallback)

**Milestone:** M1 integrations · **Labels:** `type:feature` `area:integrations` `risk:high` `prio:P0`
**Depends on:** #002, #008

## Summary
A single `LLMClient` used by both prompt flows, with OpenAI primary +
Gemini fallback, explicit (non-magical) fallback logic and bounded retries.

## Context
- `docs/architecture.md` §5 is the design spec (read it fully). Primary
  `gpt-5.6-sol`, fallback `gemini-3.6-flash`.
- Key properties: retry on **transient only** (timeout/5xx/429); auth/4xx do
  **not** retry — they fall through to fallback; `stop_after_attempt(2)` hard
  cap so no infinite loop (n8n #18797 is structurally impossible here).
- §11: each call logs `llm_model`, `tokens_in`, `tokens_out` for the cost NFR
  (<$10/month, NFR-2).

## Tasks
- [ ] `integrations/llm.py`:
  - `LLMClient(primary, fallback)` with async
    `complete(system, user, *, max_tokens) -> LLMResponse`.
  - `LLMResponse` dataclass/model: `text`, `model`, `tokens_in`, `tokens_out`.
  - `tenacity` retry on transient exceptions only.
  - On primary auth/rate/HTTP failure → log warning → call fallback.
- [ ] Emit structured cost log per call.
- [ ] A JSON-response helper (both flows parse JSON out of the completion).

## Acceptance criteria
- [ ] Primary 500 → retries then falls back to Gemini (tested in #014
      `test_llm_fallback.py`).
- [ ] Primary auth error → **no retry**, immediate fallback.
- [ ] Retry cap is hard (no unbounded loop).
- [ ] `LLMResponse.tokens_in/out` populated from provider usage fields.

## Principles (how they apply here)
See [#034](034-engineering-principles-layers.md). KISS is the tie-breaker.
- **Liskov Substitution:** the fallback (Gemini) is fully substitutable for the
  primary (OpenAI) behind one `complete()` contract — callers can't tell which
  ran.
- **Open/Closed:** a future provider is added as another client behind the same
  interface; flows and prompts don't change.
- **Dependency Inversion:** flows receive an `LLMClient`; tests inject a fake
  (#018/#027) — no provider SDK imported in flow code.
- **KISS + explicit:** fallback is explicit code, not a magic toggle; retry has
  a hard `stop_after_attempt(2)` — no clever unbounded loop (n8n #18797).

## External API reference (read before implementing)

Two SDKs, one unified `LLMClient` contract. **Read both before writing the
fallback logic** — the error type hierarchies differ and the retry/fallback
branch must catch the right exception classes from each.

### OpenAI Python SDK (primary)

- **Repo / docs:** https://github.com/openai/openai-python —
  https://platform.openai.com/docs/api-reference/chat
- **Model:** `gpt-5.6-sol` (verified current —
  https://developers.openai.com/api/docs/models/gpt-5.6-sol). Alias `gpt-5.6`
  routes here too; prefer the explicit `gpt-5.6-sol` so a future alias
  reroute can't silently change behavior.
- **Call:** `client.chat.completions.create(model=..., messages=[...],
  max_tokens=..., response_format={"type": "json_object"})` for the propose
  prompt (#015) which expects JSON.
- **Response shape** (`ChatCompletion`):
  - `choices[0].message.content` → the text we return as `LLMResponse.text`.
  - `model` → echo of the model used (use for the cost log).
  - `usage.prompt_tokens` → `LLMResponse.tokens_in`.
  - `usage.completion_tokens` → `LLMResponse.tokens_out`.
  - Source: https://github.com/openai/openai-python/blob/main/src/openai/types/chat/chat_completion.py
- **Exception hierarchy** (retry / fall-back on these specifically):
  - Source: https://github.com/openai/openai-python/blob/main/src/openai/_exceptions.py
  - `openai.APIConnectionError` / `httpx.TimeoutException` → **retry
    (transient).**
  - `openai.RateLimitError` (HTTP 429) → **retry (transient).**
  - `openai.InternalServerError` (HTTP ≥500) → **retry (transient).**
  - `openai.AuthenticationError` (HTTP 401) → **no retry, fall back
    immediately.** Bad API key is a config error; alert via #008.
  - `openai.BadRequestError` (HTTP 400), `openai.PermissionDeniedError`
    (403), `openai.NotFoundError` (404) → **no retry, no fallback** —
    these mean the request itself is wrong; raise `LLMUnavailableError`
    and alert.
  - The SDK auto-retries connection errors 2× by default — **disable that
    (`max_retries=0`) so our `tenacity` wrapper is the single retry owner.**
    Otherwise retry counts compound.

### Google GenAI Python SDK (fallback)

- **Repo / docs:** https://github.com/googleapis/python-genai —
  https://ai.google.dev/gemini-api/docs
- **Model:** `gemini-3.6-flash` (verified current — appears in the official
  token-counting example at https://ai.google.dev/gemini-api/docs/generate-content/tokens).
- **Call:** `client.models.generate_content(model=..., contents=...,
  config=types.GenerateContentConfig(system_instruction=...,
  max_output_tokens=..., response_mime_type="application/json"))`.
  - Note: Google uses `contents` (not `messages`) and `system_instruction`
    in `config` (not a system message in the list). The `LLMClient` adapter
    must translate the `(system, user)` tuple into Google's shape.
- **Response shape** (`GenerateContentResponse`):
  - `response.text` → convenience accessor for the first candidate's text
    (use this for `LLMResponse.text`).
  - `response.usage_metadata.prompt_token_count` → `tokens_in`.
  - `response.usage_metadata.candidates_token_count` → `tokens_out`.
  - `response.usage_metadata.total_token_count` → cross-check sum.
  - Source: https://ai.google.dev/gemini-api/docs/generate-content/tokens
- **Exception hierarchy:**
  - The `google-genai` SDK raises `google.genai.errors.ClientError` /
    `ServerError` subclasses. **Confirm the exact class names against the
    pinned SDK version** before writing the `except` clauses — the SDK is
    newer than OpenAI's and its error API has shifted between releases.
  - Treat 429 / 5xx / network timeouts as transient (retry).
  - Treat 400 / 401 / 403 as non-transient (raise
    `LLMUnavailableError`, alert).

### Fallback decision table (implement exactly this)

| Primary raises | Retry? | Fall back to Gemini? | Alert? |
|---|---|---|---|
| `APIConnectionError` / timeout | yes (tenacity, 2 attempts) | only after retries exhausted | yes (degraded) |
| `RateLimitError` (429) | yes | only after retries exhausted | yes (degraded) |
| `InternalServerError` (5xx) | yes | only after retries exhausted | yes (degraded) |
| `AuthenticationError` (401) | **no** | **yes, immediately** | yes (config error) |
| `BadRequestError` (400) / 403 / 404 | **no** | **no** | yes (raise `LLMUnavailableError`) |

If Gemini also fails → raise `LLMUnavailableError` (caught by the flow →
global alert, #008). Never return fabricated content.

### Open questions to resolve during implementation
- [ ] Pin exact `openai` and `google-genai` versions in `pyproject.toml`
      and confirm the exception class names above still match.
- [ ] Confirm `gpt-5.6-sol` accepts `response_format={"type":
      "json_object"}` (older GPT-5 family members did; verify for 5.6).
- [ ] Confirm `gemini-3.6-flash` honors `response_mime_type=
      "application/json"` in `GenerateContentConfig` (it does for 2.5+;
      verify for 3.6).
- [ ] Decide max_tokens default per flow (propose vs coach may differ —
      but that's a flow concern, not a client concern; client takes it as
      a parameter).

## Notes
- Same client, different prompts (#015 propose, #023 coach). Client is
  prompt-agnostic.
- No tool-calling loop in v1 (architecture §12).
