"""Admin API tests — API-key auth + dry-run trigger endpoints.

Tests prove the admin endpoints can exercise the full Flow A → Flow B pipeline
end-to-end over HTTP without Telegram:

1. Disabled when ADMIN_API_KEY is blank (router not mounted → 404).
2. 401 on missing/mismatched key.
3. POST /admin/propose — returns markdown + 5 candidates.
4. POST /admin/pick — returns created pending_review threads.
5. POST /admin/coach — returns coach feedback + lesson outcome.

All flow internals use dry_run=True: Telegram sends are skipped, but DB writes
and LLM calls still happen, so the test proves the real pipeline works.
"""

from __future__ import annotations

import datetime
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from leetcode_coach.db import models as db_models
from leetcode_coach.flows import flow_a, flow_b
from leetcode_coach.integrations.llm import LLMClient, LLMResponse
from leetcode_coach.webhooks import admin as admin_module

# --- fixtures ---------------------------------------------------------------


TEST_KEY = "test-admin-key-12345"


def _test_settings():
    return SimpleNamespace(
        admin_api_key=TEST_KEY,
        telegram_bot_token="mock",
        telegram_chat_id="123456",
        telegram_webhook_secret="",
    )


@pytest.fixture
def sqlite_session_factory(monkeypatch: pytest.MonkeyPatch):
    """In-memory SQLite patched into flow_a, flow_b, and admin modules.

    Uses StaticPool + check_same_thread=False so the engine works across
    threads (TestClient runs requests in an anyio portal thread).
    """
    engine = create_engine(
        "sqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def _get_session():
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(flow_a, "get_session", _get_session)
    monkeypatch.setattr(flow_b, "get_session", _get_session)
    monkeypatch.setattr(admin_module, "get_session", _get_session)
    return engine


@pytest.fixture
def admin_app(monkeypatch: pytest.MonkeyPatch):
    """FastAPI app with the admin router mounted + settings patched."""
    monkeypatch.setattr(admin_module, "get_settings", _test_settings)
    monkeypatch.setattr(flow_a, "get_settings", _test_settings)

    app = FastAPI()
    app.include_router(admin_module.router)
    return app


@pytest.fixture
def client(admin_app) -> TestClient:
    return TestClient(admin_app)


# --- DB helpers -------------------------------------------------------------


def _insert_problems(engine):
    """Insert unsolved problems so _gather_data has a pool to choose from."""
    with Session(engine) as session:
        for slug in ["two-sum", "merge-intervals", "binary-search", "longest-substring", "median-arrays"]:
            session.add(
                db_models.LeetCodeProblem(
                    slug=slug,
                    title=slug.replace("-", " ").title(),
                    url=f"https://leetcode.com/problems/{slug}/",
                    difficulty="medium",
                    tags="array",
                    solved=False,
                )
            )
        session.commit()


def _valid_candidates() -> list[dict]:
    """5 valid candidates (1 easy + 2 medium + 2 hard)."""
    return [
        {
            "slug": "two-sum",
            "title": "Two Sum",
            "url": "https://leetcode.com/problems/two-sum/",
            "tags": "array,hash-map",
            "difficulty": "easy",
            "reasoning": "warmup",
            "coaching_hint": "hash map",
        },
        {
            "slug": "merge-intervals",
            "title": "Merge Intervals",
            "url": "https://leetcode.com/problems/merge-intervals/",
            "tags": "array,sorting",
            "difficulty": "medium",
            "reasoning": "sort first",
            "coaching_hint": "sort then merge",
        },
        {
            "slug": "longest-substring",
            "title": "Longest Substring",
            "url": "https://leetcode.com/problems/longest-substring/",
            "tags": "hash-map",
            "difficulty": "medium",
            "reasoning": "window",
            "coaching_hint": "sliding window",
        },
        {
            "slug": "binary-search",
            "title": "Binary Search",
            "url": "https://leetcode.com/problems/binary-search/",
            "tags": "array,binary-search",
            "difficulty": "hard",
            "reasoning": "bounds",
            "coaching_hint": "pick convention",
        },
        {
            "slug": "median-arrays",
            "title": "Median of Arrays",
            "url": "https://leetcode.com/problems/median-arrays/",
            "tags": "array,binary-search",
            "difficulty": "hard",
            "reasoning": "partition",
            "coaching_hint": "partition index",
        },
    ]


def _mock_llm_propose() -> AsyncMock:
    """Mock LLMClient that returns a valid 5-candidate proposal."""
    mock = AsyncMock(spec=LLMClient)
    mock.complete = AsyncMock(
        return_value=LLMResponse(
            text=json.dumps(
                {
                    "candidate_list_markdown": "1. *Two Sum*\n2. *Merge Intervals*\n3. ...",
                    "candidates": _valid_candidates(),
                }
            ),
            model="mock-test",
            tokens_in=100,
            tokens_out=200,
        )
    )
    return mock


def _mock_llm_coach() -> AsyncMock:
    """Mock LLMClient that returns a valid coach response."""
    mock = AsyncMock(spec=LLMClient)
    mock.complete = AsyncMock(
        return_value=LLMResponse(
            text=json.dumps(
                {
                    "tutor_feedback": "Good attempt. Consider using a hash map for O(n).",
                    "lesson_title": "hash map lookup",
                    "lesson_category": "hash-map",
                    "lesson_is_recurring": False,
                    "lesson_should_graduate": False,
                    "solved": True,
                    "status": "solved",
                    "next_step": "Try the two-pointer variant next.",
                    "time_spent_min": 15,
                }
            ),
            model="mock-test",
            tokens_in=100,
            tokens_out=200,
        )
    )
    return mock


# --- Auth tests -------------------------------------------------------------


def test_propose_rejects_missing_key(client):
    """No X-Admin-Api-Key header → 401."""
    resp = client.post("/admin/propose")
    assert resp.status_code == 401


def test_propose_rejects_wrong_key(client):
    """Wrong X-Admin-Api-Key → 401."""
    resp = client.post("/admin/propose", headers={"X-Admin-Api-Key": "wrong"})
    assert resp.status_code == 401


def test_propose_accepts_valid_key(client, sqlite_session_factory, monkeypatch):
    """Valid X-Admin-Api-Key → 200."""
    _insert_problems(sqlite_session_factory)
    monkeypatch.setattr(flow_a, "LLMClient", lambda: _mock_llm_propose())
    resp = client.post("/admin/propose", headers={"X-Admin-Api-Key": TEST_KEY})
    assert resp.status_code == 200


# --- POST /admin/propose -----------------------------------------------------


def test_propose_returns_markdown_and_candidates(
    client, sqlite_session_factory, monkeypatch
):
    """POST /admin/propose returns the markdown + 5 candidates as JSON."""
    _insert_problems(sqlite_session_factory)
    monkeypatch.setattr(flow_a, "LLMClient", lambda: _mock_llm_propose())

    resp = client.post("/admin/propose", headers={"X-Admin-Api-Key": TEST_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert "Two Sum" in body["markdown"]
    assert len(body["candidates"]) == 5
    assert body["candidates"][0]["slug"] == "two-sum"
    assert body["candidates"][0]["pick_index"] == 1


def test_propose_persists_candidates_to_db(
    client, sqlite_session_factory, monkeypatch
):
    """The 5 candidates are persisted to daily_candidates (Flow B can read them)."""
    _insert_problems(sqlite_session_factory)
    monkeypatch.setattr(flow_a, "LLMClient", lambda: _mock_llm_propose())

    client.post("/admin/propose", headers={"X-Admin-Api-Key": TEST_KEY})

    today = datetime.date.today()
    with Session(sqlite_session_factory) as session:
        rows = session.exec(
            select(db_models.DailyCandidate)
            .where(db_models.DailyCandidate.proposed_at == today)
            .order_by(db_models.DailyCandidate.pick_index)
        ).all()
    assert len(rows) == 5
    assert rows[0].slug == "two-sum"


# --- POST /admin/pick --------------------------------------------------------


def test_pick_creates_pending_review_rows(
    client, sqlite_session_factory, monkeypatch
):
    """POST /admin/pick creates pending_review rows + Google Tasks, returns threads."""
    _insert_problems(sqlite_session_factory)
    monkeypatch.setattr(flow_a, "LLMClient", lambda: _mock_llm_propose())

    # First run propose to populate daily_candidates.
    client.post("/admin/propose", headers={"X-Admin-Api-Key": TEST_KEY})

    # Mock Google Tasks create_task.
    with patch.object(flow_b, "create_task", AsyncMock(return_value="task-xyz")):
        resp = client.post(
            "/admin/pick",
            json={"picks": [1, 2]},
            headers={"X-Admin-Api-Key": TEST_KEY},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["picked"]) == 2
    assert body["picked"][0]["problem_slug"] == "two-sum"
    assert body["picked"][0]["task_id"] == "task-xyz"
    assert body["picked"][0]["message_id"] == -1  # dry_run

    # Verify pending_review rows in DB.
    with Session(sqlite_session_factory) as session:
        rows = session.exec(select(db_models.PendingReview)).all()
    assert len(rows) == 2
    assert rows[0].problem_slug == "two-sum"
    assert rows[0].status == "open"


def test_pick_empty_returns_empty_list(
    client, sqlite_session_factory, monkeypatch
):
    """POST /admin/pick with no valid picks → empty list (not an error)."""
    _insert_problems(sqlite_session_factory)
    monkeypatch.setattr(flow_a, "LLMClient", lambda: _mock_llm_propose())
    client.post("/admin/propose", headers={"X-Admin-Api-Key": TEST_KEY})

    with patch.object(flow_b, "create_task", AsyncMock()):
        resp = client.post(
            "/admin/pick",
            json={"picks": []},
            headers={"X-Admin-Api-Key": TEST_KEY},
        )
    assert resp.status_code == 200
    assert resp.json()["picked"] == []


# --- POST /admin/coach -------------------------------------------------------


def test_coach_returns_feedback_and_lesson(
    client, sqlite_session_factory, monkeypatch
):
    """POST /admin/coach runs the coach pass and returns the full result."""
    _insert_problems(sqlite_session_factory)
    monkeypatch.setattr(flow_a, "LLMClient", lambda: _mock_llm_propose())

    # Propose + pick to create a pending_review row.
    client.post("/admin/propose", headers={"X-Admin-Api-Key": TEST_KEY})
    with patch.object(flow_b, "create_task", AsyncMock(return_value="task-xyz")):
        pick_resp = client.post(
            "/admin/pick",
            json={"picks": [1]},
            headers={"X-Admin-Api-Key": TEST_KEY},
        )
    pending_review_id = pick_resp.json()["picked"][0]["pending_review_id"]

    # Mock the coach LLM + Google Tasks mark_complete.
    monkeypatch.setattr(flow_b, "LLMClient", lambda: _mock_llm_coach())
    with patch.object(flow_b, "mark_complete", AsyncMock()):
        resp = client.post(
            "/admin/coach",
            json={
                "pending_review_id": pending_review_id,
                "code": "class Solution { def twoSum(self, nums, target): ... }",
            },
            headers={"X-Admin-Api-Key": TEST_KEY},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert "hash map" in body["tutor_feedback"].lower()
    assert body["solved"] is True
    assert body["status"] == "solved"
    assert body["lesson_title"] == "hash map lookup"
    assert body["lesson_category"] == "hash-map"
    assert body["lesson_action"] == "saved"
    assert body["times_reinforced"] == 1
    assert "hash map lookup" in body["reply_text"]


def test_coach_404_on_missing_pending_review(
    client, sqlite_session_factory, monkeypatch
):
    """POST /admin/coach with a non-existent pending_review_id → 404."""
    monkeypatch.setattr(flow_b, "LLMClient", lambda: _mock_llm_coach())
    with patch.object(flow_b, "mark_complete", AsyncMock()):
        resp = client.post(
            "/admin/coach",
            json={"pending_review_id": 99999, "code": "print('hello')"},
            headers={"X-Admin-Api-Key": TEST_KEY},
        )
    assert resp.status_code == 404


def test_coach_by_problem_slug(
    client, sqlite_session_factory, monkeypatch
):
    """POST /admin/coach can find the pending_review by problem_slug."""
    _insert_problems(sqlite_session_factory)
    monkeypatch.setattr(flow_a, "LLMClient", lambda: _mock_llm_propose())

    client.post("/admin/propose", headers={"X-Admin-Api-Key": TEST_KEY})
    with patch.object(flow_b, "create_task", AsyncMock(return_value="task-xyz")):
        client.post(
            "/admin/pick",
            json={"picks": [1]},
            headers={"X-Admin-Api-Key": TEST_KEY},
        )

    monkeypatch.setattr(flow_b, "LLMClient", lambda: _mock_llm_coach())
    with patch.object(flow_b, "mark_complete", AsyncMock()):
        resp = client.post(
            "/admin/coach",
            json={"problem_slug": "two-sum", "code": "solution here"},
            headers={"X-Admin-Api-Key": TEST_KEY},
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "solved"


# --- Full pipeline test ------------------------------------------------------


def test_full_pipeline_propose_pick_coach(
    client, sqlite_session_factory, monkeypatch
):
    """Full end-to-end: propose → pick → coach, all via HTTP, no Telegram."""
    _insert_problems(sqlite_session_factory)
    monkeypatch.setattr(flow_a, "LLMClient", lambda: _mock_llm_propose())

    # Step 1: propose.
    r1 = client.post("/admin/propose", headers={"X-Admin-Api-Key": TEST_KEY})
    assert r1.status_code == 200
    assert len(r1.json()["candidates"]) == 5

    # Step 2: pick problems 1 and 2.
    with patch.object(flow_b, "create_task", AsyncMock(return_value="task-xyz")):
        r2 = client.post(
            "/admin/pick",
            json={"picks": [1, 2]},
            headers={"X-Admin-Api-Key": TEST_KEY},
        )
    assert r2.status_code == 200
    assert len(r2.json()["picked"]) == 2
    pr_id = r2.json()["picked"][0]["pending_review_id"]

    # Step 3: coach the first submission.
    monkeypatch.setattr(flow_b, "LLMClient", lambda: _mock_llm_coach())
    with patch.object(flow_b, "mark_complete", AsyncMock()):
        r3 = client.post(
            "/admin/coach",
            json={"pending_review_id": pr_id, "code": "my solution"},
            headers={"X-Admin-Api-Key": TEST_KEY},
        )
    assert r3.status_code == 200
    body = r3.json()
    assert body["solved"] is True
    assert body["status"] == "solved"

    # Verify the pending_review row is now "done".
    with Session(sqlite_session_factory) as session:
        review = session.get(db_models.PendingReview, pr_id)
    assert review.status == "done"

    # Verify a leetcode_log row was inserted.
    with Session(sqlite_session_factory) as session:
        logs = session.exec(select(db_models.LeetCodeLog)).all()
    assert len(logs) == 1
    assert logs[0].status == "solved"

    # Verify the problem is now marked solved.
    with Session(sqlite_session_factory) as session:
        problem = session.get(db_models.LeetCodeProblem, "two-sum")
    assert problem.solved is True
