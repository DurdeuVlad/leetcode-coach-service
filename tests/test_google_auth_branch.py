"""Google Tasks client tests (#014) — invalid_grant branch + notes append.

Per roadmap Phase 1 exit criteria: the Google auth branch is verified to
send the *distinct* alert, not a generic crash. This suite asserts the
exact typed exception is raised (the flow layer routes it to the distinct
alert; that routing is exercised in Phase 3/#008, not here).
"""

from __future__ import annotations

import httpx
import pytest
import respx

from leetcode_coach.errors import GoogleAuthExpiredError, LeetCodeCoachError
from leetcode_coach.integrations import google_tasks

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_TASK_URL_RE = r"https://tasks\.googleapis\.com/tasks/v1/lists/.*"


def _mock_token_ok():
    return respx.post(_TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3599})
    )


@pytest.mark.asyncio
@respx.mock
async def test_invalid_grant_raises_google_auth_expired_error() -> None:
    respx.post(_TOKEN_URL).mock(
        return_value=httpx.Response(
            400,
            json={
                "error": "invalid_grant",
                "error_description": "Token has been expired or revoked.",
            },
        )
    )
    with pytest.raises(GoogleAuthExpiredError):
        await google_tasks.create_task("Two Sum", "notes", "2026-07-28")


@pytest.mark.asyncio
@respx.mock
async def test_api_401_raises_google_auth_expired_error_not_generic_crash() -> None:
    """A 401 surfaced at the API (not the token endpoint) must also route to
    the distinct GoogleAuthExpiredError, never a bare exception/500 crash."""
    _mock_token_ok()
    respx.post(url__regex=_TASK_URL_RE + r"$").mock(return_value=httpx.Response(401))
    with pytest.raises(GoogleAuthExpiredError):
        await google_tasks.create_task("Two Sum", "notes", "2026-07-28")


@pytest.mark.asyncio
@respx.mock
async def test_mark_complete_appends_not_replaces_notes() -> None:
    """BUG-2 regression: mark_complete must APPEND the new notes to the
    existing notes, never replace them."""
    _mock_token_ok()
    get_route = respx.get(url__regex=_TASK_URL_RE + r"/task-123$").mock(
        return_value=httpx.Response(200, json={"id": "task-123", "notes": "original notes"})
    )
    patch_route = respx.patch(url__regex=_TASK_URL_RE + r"/task-123$").mock(
        return_value=httpx.Response(200, json={"id": "task-123", "status": "completed"})
    )

    await google_tasks.mark_complete("task-123", notes_append="coach feedback here")

    assert get_route.called
    assert patch_route.called
    sent_body = patch_route.calls.last.request.content
    import json

    payload = json.loads(sent_body)
    assert payload["status"] == "completed"
    assert "original notes" in payload["notes"]
    assert "coach feedback here" in payload["notes"]
    assert payload["notes"].index("original notes") < payload["notes"].index("coach feedback here")


@pytest.mark.asyncio
@respx.mock
async def test_404_raises_base_coach_error_not_auth_error() -> None:
    _mock_token_ok()
    respx.get(url__regex=_TASK_URL_RE + r"/missing$").mock(return_value=httpx.Response(404))
    with pytest.raises(LeetCodeCoachError) as exc_info:
        await google_tasks.update_task("missing", notes_append="x")
    assert not isinstance(exc_info.value, GoogleAuthExpiredError)


@pytest.mark.asyncio
@respx.mock
async def test_retries_transient_500_then_succeeds() -> None:
    _mock_token_ok()
    route = respx.post(url__regex=_TASK_URL_RE + r"$").mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(200, json={"id": "task-999"}),
        ]
    )
    task_id = await google_tasks.create_task("Two Sum", "notes", "2026-07-28")
    assert task_id == "task-999"
    assert route.call_count == 2
