"""Google Tasks API client — create, update notes (append), mark complete.

Per architecture.md §6:
- `invalid_grant` from the OAuth refresh raises `GoogleAuthExpiredError`,
  caught at the flow level and routed to a distinct Telegram alert.
- It does NOT propagate to the global handler, and the coach pass is never
  told to "log with estimated defaults" (NFR-1 layer 2).

Mock-aware: if `GOOGLE_CLIENT_ID` is the placeholder `mock` or empty, all
calls log instead of hitting the API. The mock `create_task` returns a
synthetic task id so Flow B can store it in `pending_review`.

**Disabled by default for v1** (2026-07-28 decision —
docs/business-requirements.md §8 #5): GCP OAuth is discontinued to minimize
external API surfaces. All four Google env vars default to empty, which
triggers mock mode. The flows still work end-to-end; coach feedback is
delivered via the Telegram reply instead of Google Task notes. To re-enable,
set the four GOOGLE_* env vars and flip the GCP consent screen to
`In production`.

The notes-append bug fix (AGENTS.md gotcha #2): `mark_complete` takes a
`notes_append` argument and APPENDS to the existing notes, never replaces.
The n8n version dropped the coach feedback; the Python port must not.
"""

from __future__ import annotations

import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from leetcode_coach.config import get_settings
from leetcode_coach.errors import GoogleAuthExpiredError, LeetCodeCoachError

log = structlog.get_logger("google_tasks")

_TASKS_BASE = "https://tasks.googleapis.com/tasks/v1/lists/{list}/tasks"


class _TransientGoogleTasksError(Exception):
    """429/5xx/timeout — retried by tenacity. Never escapes `_call`."""


def _is_mock() -> bool:
    cid = get_settings().google_client_id
    return not cid or cid == "mock"


async def create_task(title: str, notes: str, due_date: str) -> str:
    """Create a task in the configured task list. Return its task id.

    `due_date` is an ISO date string (YYYY-MM-DD). Returns a synthetic id
    like `mock-task-<n>` in mock mode.
    """
    if _is_mock():
        import random

        task_id = f"mock-task-{random.randint(1000, 9999)}"
        log.info("create_task_mock", title=title, task_id=task_id, due=due_date)
        return task_id
    payload = {"title": title, "notes": notes, "due": f"{due_date}T00:00:00Z"}
    data = await _call("POST", "", payload)
    return str(data["id"])


async def mark_complete(task_id: str, notes_append: str | None = None) -> None:
    """Mark a task complete. If `notes_append` is given, APPEND to existing notes.

    This is the bug-fix site for the n8n notes-append bug (AGENTS.md #2):
    the n8n `mark complete` node dropped the coach feedback. We fetch the
    current notes, append the new text, and write the combined value back.
    Never replace.
    """
    if _is_mock():
        log.info("mark_complete_mock", task_id=task_id, notes_append=(notes_append or "")[:100])
        return
    # Fetch current notes (so we append, not replace).
    current = await _call("GET", f"/{task_id}", None)
    existing_notes = current.get("notes", "") or ""
    if notes_append:
        new_notes = f"{existing_notes}\n\n{notes_append}" if existing_notes else notes_append
    else:
        new_notes = existing_notes
    payload = {"id": task_id, "status": "completed", "notes": new_notes}
    await _call("PATCH", f"/{task_id}", payload)


async def update_task(task_id: str, *, notes_append: str | None = None) -> None:
    """Append to a task's notes without changing status.

    Used by the expiry sweep to mark a task "Expired without reply on <date>"
    without completing it (FR-3.2: do not delete the task — the record is useful).
    """
    if _is_mock():
        log.info("update_task_mock", task_id=task_id, notes_append=(notes_append or "")[:100])
        return
    if notes_append is None:
        return
    current = await _call("GET", f"/{task_id}", None)
    existing_notes = current.get("notes", "") or ""
    new_notes = f"{existing_notes}\n\n{notes_append}" if existing_notes else notes_append
    payload = {"id": task_id, "notes": new_notes}
    await _call("PATCH", f"/{task_id}", payload)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=10),
    retry=retry_if_exception_type(_TransientGoogleTasksError),
    reraise=True,
)
async def _call(method: str, path: str, payload: dict | None) -> dict:
    """Call the Google Tasks REST API with OAuth2 refresh-token auth.

    Raises `GoogleAuthExpiredError` on `invalid_grant` / 401 / 403 (NFR-1
    layer 2, no retry — auth is dead, not transient). Retries only on
    429/5xx/timeout. 404 (task/list missing) raises the base
    `LeetCodeCoachError` — likely config drift, not transient.
    """
    import httpx

    settings = get_settings()
    access_token = await _get_access_token()
    url = _TASKS_BASE.format(list=settings.google_tasks_list_id) + path
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            if method == "GET":
                resp = await client.get(url, headers=headers)
            elif method == "POST":
                resp = await client.post(url, headers=headers, json=payload)
            elif method == "PATCH":
                resp = await client.patch(url, headers=headers, json=payload)
            else:
                raise ValueError(f"unsupported method: {method}")
    except httpx.TimeoutException as e:
        raise _TransientGoogleTasksError(f"google tasks timeout: {e}") from e
    except httpx.HTTPError as e:
        raise _TransientGoogleTasksError(f"google tasks http error: {e}") from e

    if resp.status_code in (401, 403):
        # Could be invalid_grant surfaced at the API rather than the token
        # endpoint, or a revoked grant. Treat as auth expired either way.
        raise GoogleAuthExpiredError(f"google tasks {resp.status_code} — token expired/revoked")
    if resp.status_code == 429 or resp.status_code >= 500:
        raise _TransientGoogleTasksError(f"google tasks HTTP {resp.status_code}")
    if resp.status_code == 404:
        raise LeetCodeCoachError(f"google tasks 404 — task/list not found: {resp.text}")
    if resp.status_code >= 400:
        raise LeetCodeCoachError(f"google tasks HTTP {resp.status_code}: {resp.text}")
    return resp.json() if resp.content else {}


async def _get_access_token() -> str:
    """Refresh the OAuth2 access token using the stored refresh token.

    Raises `GoogleAuthExpiredError` on `invalid_grant` — the GCP consent
    screen Testing-mode 7-day expiry surfaces here.
    """
    import httpx

    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret,
                    "refresh_token": settings.google_refresh_token,
                    "grant_type": "refresh_token",
                },
            )
    except httpx.TimeoutException as e:
        raise _TransientGoogleTasksError(f"google oauth timeout: {e}") from e
    if resp.status_code == 400 and "invalid_grant" in resp.text:
        raise GoogleAuthExpiredError("invalid_grant — refresh token expired or revoked")
    if resp.status_code == 429 or resp.status_code >= 500:
        raise _TransientGoogleTasksError(f"google oauth HTTP {resp.status_code}")
    if resp.status_code >= 400:
        raise LeetCodeCoachError(f"google oauth HTTP {resp.status_code}: {resp.text}")
    return str(resp.json()["access_token"])
