"""Narrow contracts consumed by the agent layer.

Implement these at the domain/database boundary.  Tool handlers never accept
model-provided copies of canonical problem metadata.
"""

from __future__ import annotations

from typing import Any, Protocol, TypedDict

JsonObject = dict[str, Any]


class ProposalSelection(TypedDict):
    """The only proposal fields the model is allowed to supply."""

    slug: str
    reasoning: str
    coaching_hint: str


class CoachDomain(Protocol):
    """Domain operations available to one serialized Telegram chat run."""

    async def get_learning_profile(self, *, chat_id: int) -> JsonObject: ...

    async def search_problem_pool(
        self, *, chat_id: int, filters: JsonObject, limit: int
    ) -> list[JsonObject]: ...

    async def get_problem(self, *, chat_id: int, slug: str) -> JsonObject | None: ...

    async def get_open_queue(self, *, chat_id: int) -> JsonObject: ...

    async def get_progress(self, *, chat_id: int) -> JsonObject: ...

    async def get_walkthroughs(self, *, chat_id: int, slug: str) -> list[JsonObject]: ...

    async def draft_proposal(
        self, *, chat_id: int, selections: list[ProposalSelection]
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
        feedback: str,
        lesson_delta: JsonObject,
        operation_key: str | None = None,
    ) -> JsonObject: ...

    async def commit_canonical_attempt(
        self,
        *,
        chat_id: int,
        problem_slug: str,
        outcome: str,
        feedback: str,
        lesson_delta: JsonObject,
        operation_key: str,
        attempted_on: str | None = None,
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


class RunStateRepository(Protocol):
    """Persistence boundary for a paused Agents SDK run.

    Implementations must serialize operations per ``chat_id``.  The agent runner
    does not use this storage as a conversation transcript.
    """

    async def save(self, state: SerializedRunState) -> None: ...

    async def load(self, *, chat_id: int) -> SerializedRunState | None: ...

    async def delete(self, *, chat_id: int, run_id: str) -> None: ...

    async def abandon(self, *, chat_id: int) -> None: ...


# Deferred to avoid a runtime import cycle while retaining usable Protocol hints.
from .state import SerializedRunState  # noqa: E402
