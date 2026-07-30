"""Flow A tests (#018) — propose_5() pipeline + BUG-1 regression.

BUG-1 (docs/business-requirements.md FR-1.2): the n8n Flow A AI Agent only
saw `solved = true` rows — it never saw the unsolved pool to choose from.
The Python port reads `leetcode_problems WHERE solved = false` and passes
THAT into the prompt. The regression tests below verify:

1. `_gather_data` returns unsolved (solved=false) problems, not solved ones.
2. `_build_prompt` includes the unsolved pool in the rendered prompt text.

The full-flow test mocks the LLM (via the mock-aware LLMClient) and Telegram
(via respx) to verify the end-to-end pipeline: gather → prompt → LLM →
parse → validate → send.
"""

from __future__ import annotations

import datetime
import json
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from sqlmodel import Session, SQLModel, create_engine

from leetcode_coach.db import models as db_models
from leetcode_coach.flows import flow_a
from leetcode_coach.flows.flow_a import (
    ProposeValidationError,
    _build_prompt,
    _gather_data,
    _parse_candidates,
    _validate_candidates,
    propose_5,
)
from leetcode_coach.integrations.llm import LLMClient, LLMResponse

# --- fixtures ---------------------------------------------------------------
#
# We use an in-memory SQLite engine to test _gather_data without needing
# a real Postgres (or testcontainers/Docker). SQLModel creates all tables
# from the model definitions. The engine is patched into flow_a's module
# so `next(get_session())` uses our test engine.


@pytest.fixture
def sqlite_session_factory(monkeypatch: pytest.MonkeyPatch):
    """Replace the flow_a module's get_session with one backed by in-memory SQLite."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)

    def _get_session():
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(flow_a, "get_session", _get_session)
    # #039: the pinned refresh hook (called from propose_5 when not dry_run)
    # uses pinned_module.get_session + db.queries.get_session — patch them
    # too so the hook doesn't hit Postgres during flow_a tests.
    from leetcode_coach.db import queries as db_queries
    from leetcode_coach.flows import pinned as pinned_module

    monkeypatch.setattr(pinned_module, "get_session", _get_session)
    monkeypatch.setattr(db_queries, "get_session", _get_session)
    return engine


def _insert_problems(engine, *, solved_slugs: list[str], unsolved_slugs: list[str]):
    """Insert solved and unsolved problems into the test DB."""
    with Session(engine) as session:
        for slug in solved_slugs:
            session.add(
                db_models.LeetCodeProblem(
                    slug=slug,
                    title=slug.replace("-", " ").title(),
                    url=f"https://leetcode.com/problems/{slug}/",
                    difficulty="easy",
                    tags="array",
                    solved=True,
                )
            )
        for slug in unsolved_slugs:
            session.add(
                db_models.LeetCodeProblem(
                    slug=slug,
                    title=slug.replace("-", " ").title(),
                    url=f"https://leetcode.com/problems/{slug}/",
                    difficulty="medium",
                    tags="hash-map",
                    solved=False,
                )
            )
        session.commit()


# --- BUG-1 regression tests -------------------------------------------------


def test_bug1_gather_data_returns_unsolved_not_solved(sqlite_session_factory):
    """BUG-1 regression: _gather_data must read solved=false, NOT solved=true.

    The n8n version only fetched solved=true rows. This test verifies the
    Python port fetches the unsolved pool (solved=false) — the problems the
    LLM should be choosing FROM.
    """
    _insert_problems(
        sqlite_session_factory,
        solved_slugs=["two-sum", "reverse-linked-list"],
        unsolved_slugs=["merge-intervals", "longest-substring"],
    )
    _recent_log, unsolved_pool, _active_lessons = _gather_data()
    unsolved_slugs = {p["slug"] for p in unsolved_pool}
    assert unsolved_slugs == {"merge-intervals", "longest-substring"}
    # The solved problems must NOT appear in the unsolved pool.
    assert "two-sum" not in unsolved_slugs
    assert "reverse-linked-list" not in unsolved_slugs


def test_bug1_build_prompt_includes_unsolved_pool():
    """BUG-1 regression: the unsolved pool must appear in the rendered prompt.

    Even if _gather_data is correct, the prompt template must actually use
    the unsolved pool — not silently drop it.
    """
    unsolved = [
        {
            "slug": "merge-intervals",
            "title": "Merge Intervals",
            "url": "https://leetcode.com/problems/merge-intervals/",
            "difficulty": "medium",
            "tags": "array,sorting",
            "solved": False,
        }
    ]
    prompt = _build_prompt(
        recent_log=[],
        unsolved_pool=unsolved,
        active_lessons=[],
    )
    assert "merge-intervals" in prompt
    assert "Merge Intervals" in prompt


def test_build_prompt_includes_today_and_all_sections():
    """The prompt must contain today's date and all three JSON sections."""
    prompt = _build_prompt(
        recent_log=[{"problem_slug": "two-sum", "status": "solved"}],
        unsolved_pool=[{"slug": "merge-intervals"}],
        active_lessons=[{"title": "check empty input"}],
    )
    assert datetime.date.today().isoformat() in prompt
    assert "two-sum" in prompt
    assert "merge-intervals" in prompt
    assert "check empty input" in prompt


# --- _parse_candidates tests ------------------------------------------------


def test_parse_candidates_strips_markdown_fences():
    """Some models wrap JSON in ```json ... ``` despite the prompt saying not to."""
    payload = {
        "candidate_list_markdown": "1. *Two Sum*",
        "candidates": [],
    }
    raw = f"```json\n{json.dumps(payload)}\n```"
    markdown, candidates = _parse_candidates(raw)
    assert markdown == "1. *Two Sum*"
    assert candidates == []


def test_parse_candidates_plain_json():
    payload = {"candidate_list_markdown": "## Picks", "candidates": [{"a": 1}]}
    markdown, candidates = _parse_candidates(json.dumps(payload))
    assert markdown == "## Picks"
    assert candidates == [{"a": 1}]


def test_parse_candidates_rejects_non_json():
    with pytest.raises(ProposeValidationError, match="not valid JSON"):
        _parse_candidates("this is not json at all")


def test_parse_candidates_rejects_non_object():
    with pytest.raises(ProposeValidationError, match="not a JSON object"):
        _parse_candidates(json.dumps([1, 2, 3]))


def test_parse_candidates_rejects_missing_markdown():
    with pytest.raises(ProposeValidationError, match="candidate_list_markdown"):
        _parse_candidates(json.dumps({"candidates": []}))


def test_parse_candidates_rejects_non_list_candidates():
    with pytest.raises(ProposeValidationError, match="not a JSON array"):
        _parse_candidates(json.dumps({"candidate_list_markdown": "x", "candidates": "not a list"}))


# --- _validate_candidates tests --------------------------------------------


def _valid_candidate(slug: str, difficulty: str = "medium") -> dict:
    return {
        "slug": slug,
        "title": slug.replace("-", " ").title(),
        "url": f"https://leetcode.com/problems/{slug}/",
        "tags": "array",
        "difficulty": difficulty,
        "reasoning": "good pick",
        "coaching_hint": "think about it",
    }


def test_validate_accepts_valid_mix_2h_2m_1e():
    candidates = [
        _valid_candidate("a", "easy"),
        _valid_candidate("b", "medium"),
        _valid_candidate("c", "medium"),
        _valid_candidate("d", "hard"),
        _valid_candidate("e", "hard"),
    ]
    _validate_candidates(candidates)  # should not raise


def test_validate_accepts_3h_2m():
    candidates = [
        _valid_candidate("a", "medium"),
        _valid_candidate("b", "medium"),
        _valid_candidate("c", "hard"),
        _valid_candidate("d", "hard"),
        _valid_candidate("e", "hard"),
    ]
    _validate_candidates(candidates)  # should not raise


def test_validate_rejects_wrong_count():
    with pytest.raises(ProposeValidationError, match="expected exactly 5"):
        _validate_candidates([_valid_candidate("a")] * 4)


def test_validate_rejects_missing_key():
    c = _valid_candidate("a")
    del c["coaching_hint"]
    with pytest.raises(ProposeValidationError, match="missing keys"):
        _validate_candidates([c] + [_valid_candidate(f"b{i}") for i in range(4)])


def test_validate_rejects_invalid_difficulty():
    c = _valid_candidate("a", "extreme")
    with pytest.raises(ProposeValidationError, match="invalid difficulty"):
        _validate_candidates([c] + [_valid_candidate(f"b{i}") for i in range(4)])


def test_validate_rejects_non_leetcode_url():
    c = _valid_candidate("a")
    c["url"] = "https://example.com/problems/two-sum/"
    with pytest.raises(ProposeValidationError, match="does not look like a leetcode"):
        _validate_candidates([c] + [_valid_candidate(f"b{i}") for i in range(4)])


def test_validate_rejects_too_many_easy():
    """FR-1.3: easy is allowed only as the single spaced-repetition pick."""
    candidates = [
        _valid_candidate("a", "easy"),
        _valid_candidate("b", "easy"),
        _valid_candidate("c", "medium"),
        _valid_candidate("d", "hard"),
        _valid_candidate("e", "hard"),
    ]
    with pytest.raises(ProposeValidationError, match="difficulty mix violated"):
        _validate_candidates(candidates)


def test_validate_rejects_all_hard():
    candidates = [_valid_candidate(f"a{i}", "hard") for i in range(5)]
    with pytest.raises(ProposeValidationError, match="difficulty mix violated"):
        _validate_candidates(candidates)


# --- Full flow test (mocked LLM + Telegram) --------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_propose_5_end_to_end(sqlite_session_factory, monkeypatch):
    """Full pipeline: gather → prompt → LLM → parse → validate → send.

    Uses the in-memory SQLite DB (with unsolved problems), a mock LLMClient
    that returns a valid 5-candidate response, and respx to mock the Telegram
    sendMessage call. Verifies the markdown is sent and returned.
    """
    _insert_problems(
        sqlite_session_factory,
        solved_slugs=["two-sum"],
        unsolved_slugs=["binary-search", "merge-intervals", "median-of-two-sorted-arrays"],
    )

    # Mock LLM: return a valid 5-candidate response (1 easy + 2 medium + 2 hard).
    mock_candidates = [
        {
            "slug": "two-sum",
            "title": "Two Sum",
            "url": "https://leetcode.com/problems/two-sum/",
            "tags": "array,hash-map",
            "difficulty": "easy",
            "reasoning": "warmup",
            "coaching_hint": "trade space for time",
        },
        {
            "slug": "merge-intervals",
            "title": "Merge Intervals",
            "url": "https://leetcode.com/problems/merge-intervals/",
            "tags": "array,sorting",
            "difficulty": "medium",
            "reasoning": "sliding window",
            "coaching_hint": "sort first",
        },
        {
            "slug": "longest-substring-without-repeating-characters",
            "title": "Longest Substring",
            "url": "https://leetcode.com/problems/longest-substring-without-repeating-characters/",
            "tags": "hash-map,two-pointers",
            "difficulty": "medium",
            "reasoning": "window",
            "coaching_hint": "what leaves the set",
        },
        {
            "slug": "binary-search",
            "title": "Binary Search",
            "url": "https://leetcode.com/problems/binary-search/",
            "tags": "array,binary-search",
            "difficulty": "hard",
            "reasoning": "bounds",
            "coaching_hint": "pick your convention",
        },
        {
            "slug": "median-of-two-sorted-arrays",
            "title": "Median of Two Sorted Arrays",
            "url": "https://leetcode.com/problems/median-of-two-sorted-arrays/",
            "tags": "array,binary-search",
            "difficulty": "hard",
            "reasoning": "partition",
            "coaching_hint": "search on partition index",
        },
    ]
    mock_llm_response = LLMResponse(
        text=json.dumps(
            {
                "candidate_list_markdown": "1. *Two Sum*\n2. *Merge Intervals*\n3. ...",
                "candidates": mock_candidates,
            }
        ),
        model="mock-test",
        tokens_in=100,
        tokens_out=200,
    )
    mock_llm = AsyncMock(spec=LLMClient)
    mock_llm.complete = AsyncMock(return_value=mock_llm_response)

    # Mock Telegram sendMessage.
    respx.post(url__regex=r".*/bot.*/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})
    )

    # chat_id "123456" is accepted by the telegram_test_settings fixture in conftest.py.
    markdown = await propose_5(llm=mock_llm, chat_id="123456")
    assert "Two Sum" in markdown
    assert mock_llm.complete.called
    # Verify the system prompt was the propose prompt (contains "LeetCode coach").
    call_args = mock_llm.complete.call_args
    assert "leetcode coach" in call_args.args[0].lower()
    # Flow A sends the LLM's markdown as PLAIN TEXT (no parse_mode). Sending
    # with parse_mode="MarkdownV2" breaks on real LeetCode titles that
    # contain `-`, `.`, `(`, `)` (Telegram requires those escaped in V2).
    # Phase 9 issue #044 replaces this with an HTML card.
    tg_request = respx.calls.last.request
    tg_body = tg_request.read()
    assert b"parse_mode" not in tg_body, (
        "Flow A must send plain text (no parse_mode); MarkdownV2 breaks on "
        "real LeetCode titles. See issue #044 for the HTML card replacement."
    )


@pytest.mark.asyncio
@respx.mock
async def test_propose_5_rejects_invalid_llm_output(sqlite_session_factory):
    """If the LLM returns invalid candidates, propose_5 must fail loud (NFR-1)."""
    _insert_problems(
        sqlite_session_factory,
        solved_slugs=[],
        unsolved_slugs=["merge-intervals"],
    )

    # LLM returns only 3 candidates — validation must catch this.
    mock_llm = AsyncMock(spec=LLMClient)
    mock_llm.complete = AsyncMock(
        return_value=LLMResponse(
            text=json.dumps(
                {
                    "candidate_list_markdown": "1. ...",
                    "candidates": [_valid_candidate(f"a{i}") for i in range(3)],
                }
            ),
            model="mock",
            tokens_in=0,
            tokens_out=0,
        )
    )

    respx.post(url__regex=r".*/bot.*/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
    )

    with pytest.raises(ProposeValidationError, match="expected exactly 5"):
        await propose_5(llm=mock_llm, chat_id="123456")
