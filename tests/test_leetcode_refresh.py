"""LeetCode weekly refresh tests (#029) — refresh_pool() upsert per FR-4.

FR-4.1: pull the user's LeetCode problem history via GraphQL and upsert
into `leetcode_problems`.

The upsert must preserve user-tracked state on existing rows:
- `solved` only ever flips false→true (an AC submission confirms solved);
  never true→false.
- `times_attempted` is never decremented.
- `last_attempted` is never overwritten with None.

Test cases:
- Empty DB → all fetched rows inserted with solved=true, times_attempted=1.
- Existing row with solved=true → metadata refreshed, solved stays true,
  times_attempted preserved.
- Existing row with solved=false → metadata refreshed, solved flips to
  true (the AC list confirms it), times_attempted preserved.
- Browserless not configured → LeetCodeFetchError propagates (FR-4.2);
  no DB rows touched.
- Browserless 4xx → LeetCodeFetchError propagates; no DB rows touched.
- Mock username → canned pool upserted.
- Idempotent: a second refresh on the same canned pool is a no-op (rows
  already match).

All GraphQL calls go through the homelab Browserless `/function` endpoint
(per docs/business-requirements.md §8 #4). The direct leetcode.com/graphql/
path is no longer exercised by the code.
"""

from __future__ import annotations

import datetime

import httpx
import pytest
import respx
from sqlmodel import Session, SQLModel, create_engine, select

from leetcode_coach.db import models as db_models
from leetcode_coach.errors import LeetCodeFetchError
from leetcode_coach.integrations import leetcode

_BROWSERLESS_URL = "https://browserless.example.com"
_FUNCTION_URL = f"{_BROWSERLESS_URL}/function"


def _recent_ac_response(slugs: list[tuple[str, str]]) -> dict:
    """Build a recentAcSubmissionList response for the given (slug, title) pairs."""
    return {
        "data": {
            "recentAcSubmissionList": [
                {"id": str(i), "title": title, "titleSlug": slug, "timestamp": "1690000000"}
                for i, (slug, title) in enumerate(slugs, start=1)
            ]
        }
    }


def _metadata_response(slug: str, title: str, difficulty: str, tags: list[str]) -> dict:
    return {
        "data": {
            "problemsetQuestionList": {
                "total": 1,
                "questions": [
                    {
                        "difficulty": difficulty,
                        "title": title,
                        "titleSlug": slug,
                        "topicTags": [{"name": t, "slug": t.lower()} for t in tags],
                    }
                ],
            }
        }
    }


@pytest.fixture
def sqlite_session_factory(monkeypatch: pytest.MonkeyPatch):
    """Replace leetcode's get_session with one backed by in-memory SQLite."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)

    def _get_session():
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(leetcode, "get_session", _get_session)
    return engine


def _set_username(
    monkeypatch: pytest.MonkeyPatch,
    username: str,
    *,
    browserless_url: str = _BROWSERLESS_URL,
) -> None:
    """Patch get_settings to return a config with the given leetcode_username.

    `browserless_url` defaults to the test Browserless instance so the live
    GraphQL path is exercised; pass `""` to test the not-configured branch.
    """
    from leetcode_coach.config import Settings

    def _fake_get_settings():
        return Settings(
            telegram_bot_token="x",
            telegram_chat_id="123",
            openai_api_key="x",
            gemini_api_key="x",
            leetcode_username=username,
            browserless_url=browserless_url,
            log_level="INFO",
            timezone="Europe/Bucharest",
        )

    monkeypatch.setattr(leetcode, "get_settings", _fake_get_settings)


@pytest.mark.asyncio
@respx.mock
async def test_empty_db_inserts_all_as_solved(sqlite_session_factory, monkeypatch):
    """Empty DB → all fetched rows inserted with solved=true, times_attempted=1."""
    _set_username(monkeypatch, "realuser")
    respx.post(_FUNCTION_URL).mock(
        side_effect=[
            httpx.Response(200, json=_recent_ac_response([("two-sum", "Two Sum")])),
            httpx.Response(
                200,
                json=_metadata_response("two-sum", "Two Sum", "Easy", ["Array", "Hash Map"]),
            ),
        ]
    )
    count = await leetcode.refresh_pool()
    assert count == 1

    with Session(sqlite_session_factory) as session:
        rows = session.exec(select(db_models.LeetCodeProblem)).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.slug == "two-sum"
    assert row.title == "Two Sum"
    assert row.difficulty == "easy"
    assert "Array" in row.tags
    assert row.solved is True
    assert row.times_attempted == 1


@pytest.mark.asyncio
@respx.mock
async def test_existing_solved_row_metadata_refreshed_state_preserved(
    sqlite_session_factory, monkeypatch
):
    """Existing row with solved=true → metadata refreshed, solved stays true,
    times_attempted preserved (not reset to 1)."""
    _set_username(monkeypatch, "realuser")
    # Seed an existing row with solved=true, times_attempted=5.
    with Session(sqlite_session_factory) as session:
        session.add(
            db_models.LeetCodeProblem(
                slug="two-sum",
                title="Old Title",
                url="https://old.example/",
                difficulty="hard",  # wrong, should be refreshed
                tags="old-tag",
                solved=True,
                times_attempted=5,
                last_attempted=datetime.date(2026, 1, 1),
            )
        )
        session.commit()

    respx.post(_FUNCTION_URL).mock(
        side_effect=[
            httpx.Response(200, json=_recent_ac_response([("two-sum", "Two Sum")])),
            httpx.Response(
                200,
                json=_metadata_response("two-sum", "Two Sum", "Easy", ["Array"]),
            ),
        ]
    )
    count = await leetcode.refresh_pool()
    assert count == 1

    with Session(sqlite_session_factory) as session:
        row = session.get(db_models.LeetCodeProblem, "two-sum")
    assert row.title == "Two Sum"  # refreshed
    assert row.difficulty == "easy"  # refreshed
    assert "Array" in row.tags  # refreshed
    assert row.solved is True  # preserved
    assert row.times_attempted == 5  # preserved
    assert row.last_attempted == datetime.date(2026, 1, 1)  # preserved


@pytest.mark.asyncio
@respx.mock
async def test_existing_unsolved_row_solved_flips_to_true(sqlite_session_factory, monkeypatch):
    """Existing row with solved=false → solved flips to true (the AC list
    confirms it), times_attempted preserved."""
    _set_username(monkeypatch, "realuser")
    with Session(sqlite_session_factory) as session:
        session.add(
            db_models.LeetCodeProblem(
                slug="two-sum",
                title="Two Sum",
                url="https://leetcode.com/problems/two-sum/",
                difficulty="easy",
                tags="array",
                solved=False,
                times_attempted=3,
            )
        )
        session.commit()

    respx.post(_FUNCTION_URL).mock(
        side_effect=[
            httpx.Response(200, json=_recent_ac_response([("two-sum", "Two Sum")])),
            httpx.Response(
                200,
                json=_metadata_response("two-sum", "Two Sum", "Easy", ["Array"]),
            ),
        ]
    )
    await leetcode.refresh_pool()

    with Session(sqlite_session_factory) as session:
        row = session.get(db_models.LeetCodeProblem, "two-sum")
    assert row.solved is True  # flipped false→true
    assert row.times_attempted == 3  # preserved


@pytest.mark.asyncio
async def test_browserless_not_configured_propagates_no_db_write(
    sqlite_session_factory, monkeypatch
):
    """BROWSERLESS_URL unset → LeetCodeFetchError propagates (FR-4.2);
    no HTTP call attempted, no DB rows touched."""
    _set_username(monkeypatch, "realuser", browserless_url="")
    with pytest.raises(LeetCodeFetchError, match="BROWSERLESS_URL not configured"):
        await leetcode.refresh_pool()

    with Session(sqlite_session_factory) as session:
        rows = session.exec(select(db_models.LeetCodeProblem)).all()
    assert len(rows) == 0


@pytest.mark.asyncio
@respx.mock
async def test_browserless_4xx_propagates_no_db_write(sqlite_session_factory, monkeypatch):
    """Browserless /function 4xx → LeetCodeFetchError propagates; no DB rows touched."""
    _set_username(monkeypatch, "realuser")
    respx.post(_FUNCTION_URL).mock(return_value=httpx.Response(400, text="bad code"))
    with pytest.raises(LeetCodeFetchError, match="HTTP 400"):
        await leetcode.refresh_pool()

    with Session(sqlite_session_factory) as session:
        rows = session.exec(select(db_models.LeetCodeProblem)).all()
    assert len(rows) == 0


@pytest.mark.asyncio
async def test_mock_username_upserts_canned_pool(sqlite_session_factory, monkeypatch):
    """Mock username → canned pool upserted (no HTTP)."""
    _set_username(monkeypatch, "mock")
    count = await leetcode.refresh_pool()
    assert count >= 1

    with Session(sqlite_session_factory) as session:
        rows = session.exec(select(db_models.LeetCodeProblem)).all()
    assert len(rows) == count
    # All canned rows inserted as solved=true (the user has AC for them).
    assert all(r.solved for r in rows)


@pytest.mark.asyncio
async def test_idempotent_second_refresh_no_duplicate(sqlite_session_factory, monkeypatch):
    """A second refresh on the same canned pool is a no-op (rows already
    match) — no duplicates, no errors."""
    _set_username(monkeypatch, "mock")
    first = await leetcode.refresh_pool()
    second = await leetcode.refresh_pool()
    assert first == second
    with Session(sqlite_session_factory) as session:
        rows = session.exec(select(db_models.LeetCodeProblem)).all()
    assert len(rows) == first  # no duplicates


@pytest.mark.asyncio
@respx.mock
async def test_multiple_problems_all_upserted(sqlite_session_factory, monkeypatch):
    """2 fetched problems → 2 rows upserted, both solved=true."""
    _set_username(monkeypatch, "realuser")
    respx.post(_FUNCTION_URL).mock(
        side_effect=[
            httpx.Response(
                200,
                json=_recent_ac_response(
                    [("two-sum", "Two Sum"), ("merge-intervals", "Merge Intervals")]
                ),
            ),
            httpx.Response(
                200,
                json=_metadata_response("two-sum", "Two Sum", "Easy", ["Array"]),
            ),
            httpx.Response(
                200,
                json=_metadata_response(
                    "merge-intervals", "Merge Intervals", "Medium", ["Sorting"]
                ),
            ),
        ]
    )
    count = await leetcode.refresh_pool()
    assert count == 2

    with Session(sqlite_session_factory) as session:
        rows = session.exec(select(db_models.LeetCodeProblem)).all()
    assert len(rows) == 2
    slugs = {r.slug for r in rows}
    assert slugs == {"two-sum", "merge-intervals"}
    assert all(r.solved for r in rows)
