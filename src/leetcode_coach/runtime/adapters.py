"""SQL-backed implementations of the agent layer's narrow protocols."""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from leetcode_coach.agent.contracts import JsonObject
from leetcode_coach.clock import local_today, local_wall_to_utc
from leetcode_coach.db.models import (
    Difficulty,
    ReviewStatus,
    V2Attempt,
    V2BotState,
    V2ConversationItem,
    V2CreditLedger,
    V2FollowUp,
    V2Lesson,
    V2PendingReview,
    V2Problem,
    utcnow,
)
from leetcode_coach.domain.schemas import ProposalSelection
from leetcode_coach.domain.services import CoachDomain as SyncCoachDomain
from leetcode_coach.integrations.leetcode import exact_problem_slug, fetch_exact_problem


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", warnings=False)
    if hasattr(value, "as_dict"):
        return value.as_dict()
    if isinstance(value, dt.date | dt.datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def _clip(value: Any, limit: int) -> str:
    text = str(value or "")
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _money(value: Decimal, *, signed: bool = False) -> str:
    amount = value.quantize(Decimal("0.01"))
    return f"{amount:+.2f}" if signed else f"{amount:.2f}"


def _attempt_receipt(
    *,
    title: str,
    outcome: str,
    balance_before: Decimal,
    balance_after: Decimal,
    queued: bool,
    replayed: bool,
) -> JsonObject:
    earned = Decimal("0.00") if replayed else balance_after - balance_before
    return {
        "title": title,
        "result": outcome.capitalize(),
        "credit": _money(earned, signed=True),
        "balance": f"{_money(balance_before)} → {_money(balance_after)}",
        "path": "Open queue" if queued else "Direct attempt (no queue needed)",
        "replayed": replayed,
    }


def _problem_view(problem: V2Problem) -> JsonObject:
    return {
        "slug": problem.slug,
        "title": problem.title,
        "url": problem.url,
        "difficulty": getattr(problem.difficulty, "value", problem.difficulty),
        "tags": _clip(problem.tags, 300),
        "solved": problem.solved,
        "eligible": problem.eligible,
        "times_attempted": problem.times_attempted,
        "last_attempted": _jsonable(problem.last_attempted),
    }


def _attempt_view(attempt: V2Attempt) -> JsonObject:
    return {
        "id": attempt.id,
        "problem_slug": attempt.problem_slug,
        "attempted_on": attempt.attempted_on.isoformat(),
        "outcome": attempt.outcome,
        "feedback": _clip(attempt.feedback, 500),
        "time_spent_min": attempt.time_spent_min,
        "language": attempt.language,
        "solution_summary": _clip(attempt.solution_summary, 1000),
        "reversed_at": _jsonable(attempt.reversed_at),
    }


def _lesson_view(lesson: V2Lesson) -> JsonObject:
    return {
        "id": lesson.id,
        "title": lesson.title,
        "category": lesson.category,
        "active": lesson.active,
        "times_reinforced": lesson.times_reinforced,
    }


def _review_view(review: V2PendingReview) -> JsonObject:
    return {
        "id": review.id,
        "problem_slug": review.problem_slug,
        "batch_id": review.batch_id,
        "proposed_on": review.proposed_on.isoformat(),
        "status": getattr(review.status, "value", review.status),
    }


class SQLCoachDomainAdapter:
    """Async facade over short, transactional SQLModel sessions."""

    def __init__(self, engine) -> None:
        self.engine = engine

    async def _read(self, operation):
        def execute():
            with Session(self.engine) as session:
                return operation(session)

        return await asyncio.to_thread(execute)

    async def _write(self, operation):
        def execute():
            with Session(self.engine) as session:
                result = operation(session)
                payload = _jsonable(result)
                session.commit()
                return payload

        return await asyncio.to_thread(execute)

    async def get_learning_profile(self, *, chat_id: int) -> JsonObject:
        def query(session: Session):
            lessons = session.exec(
                select(V2Lesson)
                .where(V2Lesson.chat_id == chat_id, V2Lesson.active == True)  # noqa: E712
                .order_by(V2Lesson.times_reinforced.desc())
                .limit(20)
            ).all()
            attempts = session.exec(
                select(V2Attempt)
                .where(
                    V2Attempt.chat_id == chat_id,
                    V2Attempt.reversed_at == None,  # noqa: E711
                )
                .order_by(V2Attempt.id.desc())
                .limit(30)
            ).all()
            return {
                "active_lessons": [_lesson_view(row) for row in lessons],
                "recent_attempts": [_attempt_view(row) for row in attempts],
            }

        return await self._read(query)

    async def search_problem_catalog(
        self, *, chat_id: int, mode: str = "eligible_unsolved", filters: JsonObject, limit: int
    ) -> list[JsonObject]:
        del chat_id

        def query(session: Session):
            statement = select(V2Problem)
            if mode == "eligible_unsolved":
                statement = statement.where(
                    V2Problem.eligible == True,  # noqa: E712
                    V2Problem.solved == False,  # noqa: E712
                )
            elif mode == "solved":
                statement = statement.where(V2Problem.solved == True)  # noqa: E712
            elif mode == "ineligible":
                statement = statement.where(V2Problem.eligible == False)  # noqa: E712
            elif mode != "all":
                raise ValueError("invalid catalog mode")
            difficulties = [
                str(value).lower()
                for value in filters.get("difficulty", [])
                if str(value).lower() in {"easy", "medium", "hard"}
            ]
            if difficulties:
                statement = statement.where(V2Problem.difficulty.in_(difficulties))
            for tag in filters.get("include_tags", []):
                normalized = str(tag).strip().lower()
                if normalized:
                    statement = statement.where(func.lower(V2Problem.tags).contains(normalized))
            for tag in filters.get("exclude_tags", []):
                normalized = str(tag).strip().lower()
                if normalized:
                    statement = statement.where(~func.lower(V2Problem.tags).contains(normalized))
            topic = str(filters.get("topic") or "").strip().lower()
            if topic:
                statement = statement.where(func.lower(V2Problem.tags).contains(topic))
            rows = session.exec(
                statement.order_by(V2Problem.times_attempted.desc(), V2Problem.slug).limit(
                    max(1, min(limit, 20))
                )
            ).all()
            return [_problem_view(row) for row in rows]

        return await self._read(query)

    async def get_problem(self, *, chat_id: int, slug: str) -> JsonObject | None:
        del chat_id
        normalized = exact_problem_slug(slug)

        def query(session: Session):
            problem = session.get(V2Problem, normalized)
            return _problem_view(problem) if problem is not None else None

        existing = await self._read(query)
        if existing is not None:
            return existing

        record = await fetch_exact_problem(slug)

        def cache(session: Session):
            problem = session.get(V2Problem, record.slug)
            if problem is None:
                problem = V2Problem(
                    slug=record.slug,
                    title=record.title,
                    url=f"https://leetcode.com/problems/{record.slug}/",
                    difficulty=Difficulty(record.difficulty),
                    tags=record.tags,
                    eligible=False,
                )
                session.add(problem)
                session.flush()
            return _problem_view(problem)

        try:
            return await self._write(cache)
        except IntegrityError:
            # A scheduler refresh or concurrent lookup may have inserted the exact
            # slug after the miss. The failed session rolls back on context exit;
            # read the winning canonical row instead of surfacing a uniqueness race.
            winner = await self._read(query)
            if winner is None:
                raise
            return winner

    async def start_problem(
        self,
        *,
        chat_id: int,
        problem_slug: str,
        title: str | None,
        difficulty: str | None,
        tags: str,
    ) -> JsonObject:
        normalized = exact_problem_slug(problem_slug)

        def start(session: Session):
            problem = session.get(V2Problem, normalized)
            if problem is None:
                normalized_title = str(title or "").strip()
                normalized_difficulty = str(difficulty or "").lower()
                if not normalized_title or normalized_difficulty not in {"easy", "medium", "hard"}:
                    raise ValueError("a new problem requires title and difficulty metadata")
                problem = V2Problem(
                    slug=normalized,
                    title=normalized_title[:300],
                    url=f"https://leetcode.com/problems/{normalized}/",
                    difficulty=Difficulty(normalized_difficulty),
                    tags=tags[:1000],
                    eligible=False,
                )
                session.add(problem)
                session.flush()
            review = session.exec(
                select(V2PendingReview).where(
                    V2PendingReview.chat_id == chat_id,
                    V2PendingReview.problem_slug == normalized,
                    V2PendingReview.status == ReviewStatus.OPEN,
                )
            ).first()
            if review is None:
                review = V2PendingReview(chat_id=chat_id, problem_slug=normalized)
                session.add(review)
                session.flush()
            return _review_view(review)

        return await self._write(start)

    async def get_coaching_memory(self, *, chat_id: int) -> JsonObject:
        def query(session: Session):
            row = session.exec(
                select(V2BotState).where(
                    V2BotState.chat_id == chat_id, V2BotState.key == "coaching_memory"
                )
            ).first()
            if row is None:
                return {"version": 1}
            value = json.loads(row.value)
            return value if isinstance(value, dict) else {"version": 1}

        return await self._read(query)

    async def update_coaching_memory(self, *, chat_id: int, updates: JsonObject) -> JsonObject:
        allowed = {"goals", "preferences", "availability", "curriculum", "mastery", "notes"}
        unknown = set(updates) - allowed
        if unknown:
            raise ValueError(f"unsupported memory key: {sorted(unknown)[0]}")

        def update(session: Session):
            domain = SyncCoachDomain(session)
            row = session.exec(
                select(V2BotState).where(
                    V2BotState.chat_id == chat_id, V2BotState.key == "coaching_memory"
                )
            ).first()
            memory = json.loads(row.value) if row is not None else {"version": 1}
            memory.update(updates)
            encoded = json.dumps(memory, sort_keys=True, separators=(",", ":"))
            if len(encoded) > 20_000:
                raise ValueError("coaching memory exceeds 20000 characters")
            domain.set_bot_state(chat_id, "coaching_memory", encoded)
            return memory

        return await self._write(update)

    async def get_open_queue(self, *, chat_id: int) -> JsonObject:
        def query(session: Session):
            reviews = session.exec(
                select(V2PendingReview).where(
                    V2PendingReview.chat_id == chat_id,
                    V2PendingReview.status == ReviewStatus.OPEN,
                )
            ).all()
            return {"reviews": [_review_view(row) for row in reviews]}

        return await self._read(query)

    async def get_progress(self, *, chat_id: int) -> JsonObject:
        def query(session: Session):
            attempts = session.exec(
                select(V2Attempt)
                .where(
                    V2Attempt.chat_id == chat_id,
                    V2Attempt.reversed_at == None,  # noqa: E711
                )
                .order_by(V2Attempt.id.desc())
            ).all()
            lessons = session.exec(
                select(V2Lesson).where(V2Lesson.chat_id == chat_id, V2Lesson.active == True)  # noqa: E712
            ).all()
            balance = SyncCoachDomain(session).credit_balance(chat_id)
            dates = sorted({row.attempted_on for row in attempts}, reverse=True)
            streak = 0
            cursor = local_today()
            date_set = set(dates)
            while cursor in date_set:
                streak += 1
                cursor -= dt.timedelta(days=1)
            return {
                "recent_attempts": [_attempt_view(row) for row in attempts],
                "active_lessons": [_lesson_view(row) for row in lessons],
                "credit_balance": str(balance),
                "streak_days": streak,
            }

        return await self._read(query)

    async def search_attempt_history(
        self, *, chat_id: int, filters: JsonObject, limit: int
    ) -> list[JsonObject]:
        def query(session: Session):
            statement = select(V2Attempt).where(V2Attempt.chat_id == chat_id)
            if not filters.get("include_reversed", False):
                statement = statement.where(V2Attempt.reversed_at == None)  # noqa: E711
            if filters.get("problem_slug"):
                statement = statement.where(
                    V2Attempt.problem_slug == exact_problem_slug(str(filters["problem_slug"]))
                )
            if filters.get("outcome"):
                statement = statement.where(V2Attempt.outcome == str(filters["outcome"]))
            rows = session.exec(
                statement.order_by(V2Attempt.id.desc()).limit(min(max(limit, 1), 50))
            ).all()
            return [_attempt_view(row) for row in rows]

        return await self._read(query)

    async def publish_practice_set(
        self, *, chat_id: int, selections: list[JsonObject], operation_key: str
    ) -> JsonObject:
        if len(selections) > 20:
            raise ValueError("a proposal supports at most 20 candidates for Telegram transport")
        parsed = [
            ProposalSelection(
                slug=exact_problem_slug(str(item.get("slug", ""))),
                reasoning=str(item.get("reasoning", ""))[:2000],
                coaching_hint=str(item.get("coaching_hint", ""))[:2000],
                title=str(item.get("title", "")).strip()[:300] or None,
                difficulty=str(item.get("difficulty", "")).strip().lower() or None,
                tags=str(item.get("tags", ""))[:1000],
            )
            for item in selections
        ]

        def create():
            with Session(self.engine) as session:
                replay_key = (
                    "proposal_publish:"
                    + hashlib.sha256(f"{chat_id}:{operation_key}".encode()).hexdigest()
                )
                replay = session.exec(
                    select(V2BotState).where(
                        V2BotState.chat_id == chat_id, V2BotState.key == replay_key
                    )
                ).first()
                if replay is not None:
                    return {"batch_id": int(replay.value), "replayed": True}
                if not parsed:
                    raise ValueError("a proposal requires at least one selection")
                for selection in parsed:
                    problem = session.get(V2Problem, selection.slug)
                    if problem is None:
                        if selection.title is None or selection.difficulty not in {
                            "easy",
                            "medium",
                            "hard",
                        }:
                            raise ValueError(
                                "a new proposal problem requires title and difficulty metadata"
                            )
                        session.add(
                            V2Problem(
                                slug=selection.slug,
                                title=selection.title,
                                url=f"https://leetcode.com/problems/{selection.slug}/",
                                difficulty=Difficulty(selection.difficulty),
                                tags=selection.tags,
                                eligible=False,
                            )
                        )
                session.flush()
                domain = SyncCoachDomain(session)
                batch, preview = domain.create_proposal(chat_id, parsed)
                domain.set_bot_state(chat_id, replay_key, str(batch.id))
                session.commit()
                return {"batch_id": batch.id, **preview.as_dict()}

        return await asyncio.to_thread(create)

    async def commit_picks(self, *, chat_id: int, batch_id: str, slugs: list[str]) -> JsonObject:
        return await self._write(
            lambda session: SyncCoachDomain(session).commit_picks(chat_id, int(batch_id), slugs)
        )

    async def commit_attempt(
        self,
        *,
        chat_id: int,
        review_id: str,
        outcome: str,
        feedback: str,
        lesson_delta: JsonObject,
        operation_key: str | None = None,
        language: str | None = None,
        solution_summary: str = "",
        time_spent_min: int | None = None,
    ) -> JsonObject:
        def commit(session: Session):
            review = session.get(V2PendingReview, int(review_id))
            problem = session.get(V2Problem, review.problem_slug) if review is not None else None
            domain = SyncCoachDomain(session)
            balance_before = domain.credit_balance(chat_id)
            result = domain.commit_attempt(
                chat_id,
                int(review_id),
                outcome,
                feedback,
                lesson_delta,
                operation_key=operation_key,
                language=language,
                solution_summary=solution_summary,
                time_spent_min=time_spent_min,
            )
            replayed = isinstance(result, dict) and result.get("replayed") is True
            recorded_outcome = outcome
            if replayed and operation_key is not None:
                key = f"review_attempt:{chat_id}:{review_id}:{operation_key}"
                entry = session.exec(
                    select(V2CreditLedger).where(V2CreditLedger.idempotency_key == key)
                ).one()
                recorded_outcome = entry.reason
            balance_after = domain.credit_balance(chat_id)
            payload = _jsonable(result)
            assert isinstance(payload, dict)
            assert problem is not None
            return {
                **payload,
                "receipt": _attempt_receipt(
                    title=problem.title,
                    outcome=recorded_outcome,
                    balance_before=balance_before,
                    balance_after=balance_after,
                    queued=True,
                    replayed=replayed,
                ),
            }

        return await self._write(commit)

    async def record_problem_attempt(
        self,
        *,
        chat_id: int,
        problem_slug: str,
        title: str | None = None,
        difficulty: str | None = None,
        tags: str = "",
        outcome: str,
        feedback: str,
        lesson_delta: JsonObject,
        operation_key: str,
        attempted_on: str | None = None,
        language: str | None = None,
        solution_summary: str = "",
        time_spent_min: int | None = None,
    ) -> JsonObject:
        normalized = exact_problem_slug(problem_slug)
        if len(normalized) > 200:
            raise ValueError("problem slug must contain at most 200 characters")
        if title is not None and len(title) > 300:
            raise ValueError("problem title must contain at most 300 characters")
        if len(tags) > 1_000:
            raise ValueError("problem tags must contain at most 1000 characters")
        parsed_date = dt.date.fromisoformat(attempted_on) if attempted_on else local_today()
        if parsed_date > local_today():
            raise ValueError("attempted_on cannot be in the future")

        def commit(session: Session):
            problem = session.get(V2Problem, normalized)
            if problem is None:
                normalized_difficulty = str(difficulty or "").lower()
                normalized_title = str(title or "").strip()
                if not normalized_title or normalized_difficulty not in {"easy", "medium", "hard"}:
                    raise ValueError(
                        "a new problem requires title and easy, medium, or hard difficulty metadata"
                    )
                problem = V2Problem(
                    slug=normalized,
                    title=normalized_title,
                    url=f"https://leetcode.com/problems/{normalized}/",
                    difficulty=Difficulty(normalized_difficulty),
                    tags=str(tags),
                    eligible=False,
                )
                session.add(problem)
                session.flush()
            domain = SyncCoachDomain(session)
            balance_before = domain.credit_balance(chat_id)
            result = domain.record_problem_attempt(
                chat_id,
                normalized,
                outcome,
                feedback,
                lesson_delta,
                operation_key=operation_key,
                attempted_on=parsed_date,
                language=language,
                solution_summary=solution_summary[:4000],
                time_spent_min=time_spent_min,
            )
            replayed = isinstance(result, dict) and result.get("replayed") is True
            queued = getattr(result, "review_id", None) is not None
            recorded_outcome = outcome
            if replayed:
                key = f"canonical_attempt:{chat_id}:{normalized}:{operation_key}"
                entry = session.exec(
                    select(V2CreditLedger).where(V2CreditLedger.idempotency_key == key)
                ).one()
                queued = entry.review_id is not None
                recorded_outcome = entry.reason
            balance_after = domain.credit_balance(chat_id)
            payload = _jsonable(result)
            assert isinstance(payload, dict)
            assert problem is not None
            return {
                **payload,
                "receipt": _attempt_receipt(
                    title=problem.title,
                    outcome=recorded_outcome,
                    balance_before=balance_before,
                    balance_after=balance_after,
                    queued=queued,
                    replayed=replayed,
                ),
            }

        return await self._write(commit)

    async def correct_attempt(
        self,
        *,
        chat_id: int,
        attempt_id: str,
        outcome: str | None,
        attempted_on: str | None,
        feedback: str | None,
        language: str | None,
        clear_language: bool = False,
        solution_summary: str | None,
        time_spent_min: int | None,
        clear_time_spent: bool = False,
        reason: str,
        operation_key: str,
    ) -> JsonObject:
        parsed = dt.date.fromisoformat(attempted_on) if attempted_on else None
        if parsed is not None and parsed > local_today():
            raise ValueError("attempted_on cannot be in the future")
        return await self._write(
            lambda session: SyncCoachDomain(session).correct_attempt(
                chat_id,
                int(attempt_id),
                outcome=outcome,
                attempted_on=parsed,
                feedback=feedback[:4000] if feedback is not None else None,
                language=language[:50] if language else None,
                clear_language=clear_language,
                solution_summary=solution_summary[:4000] if solution_summary is not None else None,
                time_spent_min=time_spent_min,
                clear_time_spent=clear_time_spent,
                reason=reason[:1000],
                operation_key=operation_key,
            )
        )

    async def reverse_attempt(
        self,
        *,
        chat_id: int,
        attempt_id: str,
        reason: str,
        operation_key: str,
    ) -> JsonObject:
        return await self._write(
            lambda session: SyncCoachDomain(session).reverse_attempt(
                chat_id,
                int(attempt_id),
                reason=reason[:1000],
                operation_key=operation_key,
            )
        )

    async def schedule_follow_up(
        self, *, chat_id: int, due_at: str, message: str, operation_key: str
    ) -> JsonObject:
        local_due = dt.datetime.fromisoformat(due_at)
        utc_due = (
            local_due.astimezone(dt.UTC)
            if local_due.tzinfo
            else local_wall_to_utc(local_due.isoformat())
        )
        if utc_due <= utcnow():
            raise ValueError("follow-up must be scheduled in the future")
        if not message.strip() or len(message) > 4000:
            raise ValueError("follow-up message must contain 1 to 4000 characters")
        return await self._write(
            lambda session: SyncCoachDomain(session).schedule_follow_up(
                chat_id, utc_due, message.strip(), operation_key
            )
        )

    async def list_follow_ups(
        self, *, chat_id: int, status: str = "scheduled", limit: int = 20
    ) -> list[JsonObject]:
        def query(session: Session):
            statement = select(V2FollowUp).where(V2FollowUp.chat_id == chat_id)
            if status != "all":
                statement = statement.where(V2FollowUp.status == status)
            return list(
                session.exec(
                    statement.order_by(V2FollowUp.due_at).limit(min(max(limit, 1), 50))
                ).all()
            )

        return await self._read(query)

    async def cancel_follow_up(
        self, *, chat_id: int, follow_up_id: str, operation_key: str
    ) -> JsonObject:
        return await self._write(
            lambda session: SyncCoachDomain(session).cancel_follow_up(
                chat_id, follow_up_id, operation_key
            )
        )

    async def skip_problem(self, *, chat_id: int, review_id: str) -> JsonObject:
        return await self._write(
            lambda session: SyncCoachDomain(session).skip_problem(chat_id, int(review_id))
        )

    async def mark_solution_viewed(self, *, chat_id: int, review_id: str) -> JsonObject:
        return await self._write(
            lambda session: SyncCoachDomain(session).mark_solution_viewed(chat_id, int(review_id))
        )

    async def reattempt_problem(self, *, chat_id: int, review_id: str) -> JsonObject:
        return await self._write(
            lambda session: SyncCoachDomain(session).reattempt_problem(chat_id, int(review_id))
        )

    async def extend_proposal(
        self, *, chat_id: int, batch_id: str, operation_key: str | None = None
    ) -> JsonObject:
        return await self._write(
            lambda session: SyncCoachDomain(session).extend_proposal(
                chat_id, int(batch_id), operation_key=operation_key
            )
        )

    async def accept_credit_deficit(self, *, chat_id: int, date: str) -> JsonObject:
        return await self._write(
            lambda session: SyncCoachDomain(session).accept_credit_deficit(
                chat_id, dt.date.fromisoformat(date)
            )
        )

    async def adjust_lesson(
        self,
        *,
        chat_id: int,
        lesson_delta: JsonObject,
        operation_key: str | None = None,
    ) -> JsonObject:
        return await self._write(
            lambda session: SyncCoachDomain(session).adjust_lesson(
                chat_id, lesson_delta, operation_key=operation_key
            )
        )


class PostgresAgentSession:
    """Agents SDK Session backed by V2 conversation items."""

    session_settings = None

    def __init__(self, engine, chat_id: int) -> None:
        self.engine = engine
        self.chat_id = chat_id
        self.session_id = f"telegram:{chat_id}"

    async def get_items(self, limit: int | None = None) -> list[dict[str, Any]]:
        def execute():
            with Session(self.engine) as session:
                statement = (
                    select(V2ConversationItem)
                    .where(V2ConversationItem.chat_id == self.chat_id)
                    .order_by(V2ConversationItem.sequence.desc())
                )
                if limit is not None:
                    statement = statement.limit(limit)
                rows = session.exec(statement).all()
                return [json.loads(row.content) for row in reversed(rows)]

        return await asyncio.to_thread(execute)

    async def add_items(self, items: list[dict[str, Any]]) -> None:
        def execute():
            with Session(self.engine) as session:
                current = session.exec(
                    select(func.coalesce(func.max(V2ConversationItem.sequence), 0)).where(
                        V2ConversationItem.chat_id == self.chat_id
                    )
                ).one()
                for offset, item in enumerate(items, 1):
                    session.add(
                        V2ConversationItem(
                            chat_id=self.chat_id,
                            sequence=int(current) + offset,
                            role=str(item.get("role") or item.get("type") or "item")[:20],
                            content=json.dumps(item, default=str),
                        )
                    )
                session.commit()

        await asyncio.to_thread(execute)

    async def pop_item(self) -> dict[str, Any] | None:
        def execute():
            with Session(self.engine) as session:
                row = session.exec(
                    select(V2ConversationItem)
                    .where(V2ConversationItem.chat_id == self.chat_id)
                    .order_by(V2ConversationItem.sequence.desc())
                ).first()
                if row is None:
                    return None
                value = json.loads(row.content)
                session.delete(row)
                session.commit()
                return value

        return await asyncio.to_thread(execute)

    async def clear_session(self) -> None:
        await asyncio.to_thread(self._clear)

    def _clear(self) -> None:
        with Session(self.engine) as session:
            session.exec(
                delete(V2ConversationItem).where(V2ConversationItem.chat_id == self.chat_id)
            )
            session.commit()
