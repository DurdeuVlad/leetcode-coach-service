"""Narrow contracts consumed by the agent layer.

Implement these at the domain/database boundary. Exact problem identity and
agent-supplied metadata are normalized before durable operations.
"""

from __future__ import annotations

from typing import Any, NotRequired, Protocol, TypedDict

JsonObject = dict[str, Any]


class ProposalSelection(TypedDict):
    """Problem identity and coaching copy chosen by the agent."""

    slug: str
    title: NotRequired[str]
    difficulty: NotRequired[str]
    tags: NotRequired[str]
    reasoning: str
    coaching_hint: str


class CoachDomain(Protocol):
    """Domain operations available to one serialized Telegram chat run."""

    async def get_learning_profile(self, *, chat_id: int) -> JsonObject: ...

    async def search_problem_catalog(
        self, *, chat_id: int, mode: str, filters: JsonObject, limit: int
    ) -> list[JsonObject]: ...

    async def get_problem(self, *, chat_id: int, slug: str) -> JsonObject | None: ...

    async def start_problem(
        self,
        *,
        chat_id: int,
        problem_slug: str,
        title: str | None,
        difficulty: str | None,
        tags: str,
    ) -> JsonObject: ...

    async def get_coaching_memory(self, *, chat_id: int) -> JsonObject: ...

    async def update_coaching_memory(self, *, chat_id: int, updates: JsonObject) -> JsonObject: ...

    async def get_open_queue(self, *, chat_id: int) -> JsonObject: ...

    async def get_progress(self, *, chat_id: int) -> JsonObject: ...

    async def search_attempt_history(
        self, *, chat_id: int, filters: JsonObject, limit: int
    ) -> list[JsonObject]: ...

    async def publish_practice_set(
        self, *, chat_id: int, selections: list[ProposalSelection], operation_key: str
    ) -> JsonObject: ...

    async def commit_picks(
        self, *, chat_id: int, batch_id: str, slugs: list[str]
    ) -> JsonObject: ...

    async def commit_attempt(
        self,
        *,
        chat_id: int,
        review_id: str,
        outcome: str,
        feedback: str = "",
        lesson_delta: JsonObject | None = None,
        operation_key: str | None = None,
        language: str | None = None,
        solution_summary: str = "",
        time_spent_min: int | None = None,
    ) -> JsonObject: ...

    async def record_problem_attempt(
        self,
        *,
        chat_id: int,
        problem_slug: str,
        title: str | None = None,
        difficulty: str | None = None,
        tags: str = "",
        outcome: str,
        feedback: str = "",
        lesson_delta: JsonObject | None = None,
        attempted_on: str | None = None,
        operation_key: str,
        language: str | None = None,
        solution_summary: str = "",
        time_spent_min: int | None = None,
    ) -> JsonObject: ...

    async def correct_attempt(self, **kwargs: Any) -> JsonObject: ...

    async def reverse_attempt(self, **kwargs: Any) -> JsonObject: ...

    async def schedule_follow_up(
        self, *, chat_id: int, due_at: str, message: str, operation_key: str
    ) -> JsonObject: ...

    async def list_follow_ups(
        self, *, chat_id: int, status: str, limit: int
    ) -> list[JsonObject]: ...

    async def cancel_follow_up(
        self, *, chat_id: int, follow_up_id: str, operation_key: str
    ) -> JsonObject: ...

    async def skip_problem(self, *, chat_id: int, review_id: str) -> JsonObject: ...

    async def mark_solution_viewed(self, *, chat_id: int, review_id: str) -> JsonObject: ...

    async def reattempt_problem(self, *, chat_id: int, review_id: str) -> JsonObject: ...

    async def extend_proposal(
        self, *, chat_id: int, batch_id: str, operation_key: str | None = None
    ) -> JsonObject: ...

    async def accept_credit_deficit(self, *, chat_id: int, date: str) -> JsonObject: ...

    async def adjust_lesson(
        self,
        *,
        chat_id: int,
        lesson_delta: JsonObject,
        operation_key: str | None = None,
    ) -> JsonObject: ...
