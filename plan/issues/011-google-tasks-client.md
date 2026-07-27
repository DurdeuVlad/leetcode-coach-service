# #011 — Google Tasks client (invalid_grant + notes_append)

**Milestone:** M1 integrations · **Labels:** `type:feature` `type:bug-fix` `area:integrations` `risk:high` `prio:P0`
**Depends on:** #002, #008

## Summary
Google Tasks client for create/update/complete, with the typed `invalid_grant`
branch and the **append-not-replace** notes behavior (BUG-2 groundwork).

## Context
- `docs/architecture.md` §6 is the design spec (OAuth2 refresh-token flow;
  `RefreshError` containing `invalid_grant` → raise `GoogleAuthExpiredError`).
- `docs/business-requirements.md` FR-2.4 (create), FR-2.7.3 (complete + append
  notes), FR-3.2 (expiry appends notes, never deletes).
- **BUG-2:** the n8n `mark complete` node **replaced** notes, dropping coach
  feedback. This client must **append**.

## Tasks
- [ ] `integrations/google_tasks.py`:
  - `create_task(title, notes, due) -> task_id`.
  - `update_task(task_id, *, notes_append=None, ...)`.
  - `mark_complete(task_id, *, notes_append)` — get current notes, **append**
    `notes_append`, set status complete.
- [ ] Wrap refresh in try/except: `invalid_grant` → `GoogleAuthExpiredError`
      (do not swallow, do not "log defaults").
- [ ] `tenacity` retry on transient HTTP only.

## Acceptance criteria
- [ ] `mark_complete` preserves existing notes and appends the new text
      (verified in #014).
- [ ] `RefreshError("invalid_grant")` surfaces as `GoogleAuthExpiredError`
      (verified in #014 `test_google_auth_branch.py`).
- [ ] No code path fabricates/estimates data on failure.

## Principles (how they apply here)
See [#034](034-engineering-principles-layers.md). KISS is the tie-breaker.
- **Fail loud, fail typed:** `invalid_grant` becomes `GoogleAuthExpiredError` —
  never swallowed, never "logged with estimated defaults".
- **Single Responsibility:** this client only speaks Google Tasks; it does not
  decide expiry vs. completion (flows do) — it just exposes the operations.
- **KISS:** `mark_complete` does the minimal get→append→complete; the
  append-not-replace fix (BUG-2) is the simplest correct behavior.
- **Interface Segregation:** narrow verbs (`create_task`, `update_task`,
  `mark_complete`) so callers pull only what they use.

## External API reference (read before implementing)

**Primary source:** Google Tasks API REST reference —
https://developers.google.com/tasks/reference/rest/v1
**Python quickstart (auth flow):**
https://developers.google.com/workspace/tasks/quickstart/python
**OAuth2 refresh-token flow:**
https://developers.google.com/identity/protocols/oauth2/web-server#httprest

### Endpoints to call (exact paths + field shapes)

All paths are under `https://tasks.googleapis.com/tasks/v1/lists/{tasklist}/`.
The `{tasklist}` is the task list ID — use `@default` for the user's
default list (configurable via `GOOGLE_TASKS_LIST_ID` env var, default
`@default`).

- **`POST .../tasks`** (`tasks.insert`) — create a task.
  - Doc: https://developers.google.com/tasks/reference/rest/v1/tasks/insert
  - Request body (`Task` resource —
    https://developers.google.com/tasks/reference/rest/v1/tasks#resource):
    `title`, `notes`, `due` (RFC 3339, e.g. `2026-07-28T00:00:00Z`).
  - Response: the created `Task` with its `id` — return this as `task_id`.
  - Scope: `https://www.googleapis.com/auth/tasks` (read/write). The
    `tasks.readonly` scope is **not enough** for create/update/complete.

- **`PATCH .../tasks/{task}`** (`tasks.patch`) — partial update.
  - Doc: https://developers.google.com/tasks/reference/rest/v1/tasks/patch
  - Use this for `update_task(*, notes_append=None, ...)`.
  - **BUG-2 fix:** to append notes, first `GET` the current task, read its
    `notes` field, concatenate `current_notes + "\n\n" + notes_append`,
    then `PATCH` with the combined string. The API has no native append —
    a bare `PATCH notes=new` would **replace**, reintroducing BUG-2.

- **`POST .../tasks/{task}/complete`** is **not** a real endpoint. To mark
  complete: `PATCH .../tasks/{task}` with body `{"status": "completed"}`
  (and the appended notes, if any). The `status` field accepts
  `"needsAction"` or `"completed"` — see the `Task` resource doc above.

- **`GET .../tasks/{task}`** (`tasks.get`) — fetch a single task to read
  its current `notes` before append. Doc:
  https://developers.google.com/tasks/reference/rest/v1/tasks/get

### OAuth2 refresh-token flow + the `invalid_grant` branch

- We use the **installed-app / desktop** OAuth2 client (single-user
  personal use — `docs/architecture.md` §6). The refresh token is stored
  as `GOOGLE_REFRESH_TOKEN` env var; client id/secret as
  `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`.
- Refresh is a POST to `https://oauth2.googleapis.com/token` with
  `grant_type=refresh_token`, `refresh_token`, `client_id`, `client_secret`.
- **On success:** returns `{"access_token": "...", "expires_in": 3599, ...}`.
- **On failure:** returns HTTP 400 with body
  `{"error": "invalid_grant", "error_description": "Token has been expired or revoked."}`.
  This is the exact signal NFR-1 layer 2 must catch → raise
  `GoogleAuthExpiredError` → distinct alert (#008).
- **Root cause of the recurring 7-day expiry:** the GCP OAuth consent
  screen is in `Testing` status — refresh tokens for "Testing" apps expire
  after 7 days. The fix is flipping the consent screen to `In production`
  (single-user personal use, no verification needed). That's Phase 6 /
  `docs/architecture.md` §6 — this issue just handles the failure
  **gracefully** when it happens.

### Library choice

The reference quickstart uses `google-api-python-client` +
`google-auth-oauthlib`. That works but is heavy. For our use case (4
endpoints, all JSON), **prefer `httpx` directly** with the access token
from a manual refresh — it matches the other clients' style, is testable
with `respx` (#014), and avoids the `google-api-python-client` discovery
cache. If we go this route, the refresh logic is ~15 lines of `httpx`.

If we do use `google-api-python-client`, the `invalid_grant` surfaces as
`google.auth.exceptions.RefreshError` — catch that, inspect
`error_description` / `response.json()["error"]`, and raise
`GoogleAuthExpiredError` only on `invalid_grant` (other refresh errors →
retry then alert).

### Error / retry surface

- HTTP 5xx, 429, `httpx.TimeoutException` → tenacity retry (transient).
- HTTP 400 with `error: "invalid_grant"` → `GoogleAuthExpiredError`,
  **no retry**, distinct alert.
- HTTP 401 / 403 → `GoogleAuthExpiredError` (treat as auth dead, same
  alert path — re-auth needed).
- HTTP 404 (task list or task missing) → raise `CoachError` subclass,
  alert — likely a config drift, not transient.

### Open questions to resolve during implementation
- [ ] `httpx`-direct vs `google-api-python-client` — pick one and document
      the choice in the module docstring. (Recommendation: `httpx`-direct
      for symmetry with the other clients.)
- [ ] Confirm `@default` resolves correctly for the operator's account
      (it does for consumer Google accounts; verify before relying on it).
- [ ] Decide notes separator: `"\n\n"` vs `"\n---\n"`. The n8n BUG-2
      reference used plain append; pick `"\n\n"` for simplicity unless the
      coach output already has structure that warrants a divider.

## Notes
- The flow-level routing of `GoogleAuthExpiredError` to the distinct alert
  is exercised in #014 and used by #026/#028.
