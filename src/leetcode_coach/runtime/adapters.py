"""SQL-backed implementations of the agent layer's narrow protocols."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import secrets
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from leetcode_coach.agent.contracts import JsonObject
from leetcode_coach.agent.state import PendingApproval, SerializedRunState
from leetcode_coach.db.models import (
    AgentRunStatus,
    ApprovalStatus,
    Difficulty,
    ReviewStatus,
    V2AgentRun,
    V2Attempt,
    V2ConversationItem,
    V2CreditLedger,
    V2Lesson,
    V2PendingApproval,
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
                .where(V2Attempt.chat_id == chat_id)
                .order_by(V2Attempt.id.desc())
                .limit(30)
            ).all()
            return {
                "active_lessons": [_lesson_view(row) for row in lessons],
                "recent_attempts": [_attempt_view(row) for row in attempts],
            }

        return await self._read(query)

    async def search_problem_pool(
        self, *, chat_id: int, filters: JsonObject, limit: int
    ) -> list[JsonObject]:
        del chat_id

        def query(session: Session):
            statement = select(V2Problem).where(
                V2Problem.eligible == True,  # noqa: E712
                V2Problem.solved == False,  # noqa: E712
            )
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
                .where(V2Attempt.chat_id == chat_id)
                .order_by(V2Attempt.id.desc())
                .limit(20)
            ).all()
            lessons = session.exec(
                select(V2Lesson).where(V2Lesson.chat_id == chat_id, V2Lesson.active == True)  # noqa: E712
            ).all()
            balance = SyncCoachDomain(session).credit_balance(chat_id)
            dates = sorted({row.attempted_on for row in attempts}, reverse=True)
            streak = 0
            cursor = dt.date.today()
            for attempted_on in dates:
                if attempted_on == cursor:
                    streak += 1
                    cursor -= dt.timedelta(days=1)
                elif attempted_on < cursor:
                    break
            return {
                "recent_attempts": [_attempt_view(row) for row in attempts],
                "active_lessons": [_lesson_view(row) for row in lessons],
                "credit_balance": str(balance),
                "streak_days": streak,
            }

        return await self._read(query)

    async def get_walkthroughs(self, *, chat_id: int, slug: str) -> list[JsonObject]:
        # V2 intentionally has no Browserless/SearXNG dependency. Keep the
        # typed tool deterministic until a first-party tutorial source exists.
        await self.get_problem(chat_id=chat_id, slug=slug)
        return []

    async def draft_proposal(self, *, chat_id: int, selections: list[JsonObject]) -> JsonObject:
        parsed = [
            ProposalSelection(
                slug=str(item.get("slug", "")),
                reasoning=str(item.get("reasoning", ""))[:2000],
                coaching_hint=str(item.get("coaching_hint", ""))[:2000],
            )
            for item in selections
        ]

        def create():
            with Session(self.engine) as session:
                if len(parsed) != 5:
                    raise ValueError("a proposal requires exactly five selections")
                domain = SyncCoachDomain(session)
                preview = domain.draft_proposal(parsed)
                counts = {"medium": 0, "hard": 0}
                for candidate in preview.candidates:
                    if candidate.difficulty in counts:
                        counts[candidate.difficulty] += 1
                if counts["medium"] not in {2, 3} or counts["hard"] not in {2, 3}:
                    raise ValueError("proposal requires 2-3 medium and 2-3 hard canonical problems")
                batch, preview = domain.create_proposal(chat_id, parsed)
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

    async def commit_canonical_attempt(
        self,
        *,
        chat_id: int,
        problem_slug: str,
        outcome: str,
        feedback: str,
        lesson_delta: JsonObject,
        operation_key: str,
    ) -> JsonObject:
        def commit(session: Session):
            problem = session.get(V2Problem, problem_slug)
            domain = SyncCoachDomain(session)
            balance_before = domain.credit_balance(chat_id)
            result = domain.commit_canonical_attempt(
                chat_id,
                problem_slug,
                outcome,
                feedback,
                lesson_delta,
                operation_key=operation_key,
            )
            replayed = isinstance(result, dict) and result.get("replayed") is True
            queued = getattr(result, "review_id", None) is not None
            recorded_outcome = outcome
            if replayed:
                key = f"canonical_attempt:{chat_id}:{problem_slug}:{operation_key}"
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


class SQLRunStateRepository:
    """Persists opaque Agents SDK RunState plus Telegram-safe approval aliases."""

    def __init__(self, engine) -> None:
        self.engine = engine

    async def save(self, state: SerializedRunState) -> None:
        def execute():
            with Session(self.engine) as session:
                old_runs = session.exec(
                    select(V2AgentRun).where(
                        V2AgentRun.chat_id == state.chat_id,
                        V2AgentRun.status == AgentRunStatus.PAUSED,
                    )
                ).all()
                for old in old_runs:
                    old.status = AgentRunStatus.FAILED
                    old.completed_at = utcnow()
                    old_approvals = session.exec(
                        select(V2PendingApproval).where(
                            V2PendingApproval.agent_run_id == old.id,
                            V2PendingApproval.status == ApprovalStatus.PENDING,
                        )
                    ).all()
                    for approval in old_approvals:
                        approval.status = ApprovalStatus.EXPIRED
                        approval.resolved_at = utcnow()
                row = V2AgentRun(
                    id=state.run_id,
                    chat_id=state.chat_id,
                    status=AgentRunStatus.PAUSED,
                    state_json=json.dumps(
                        {
                            "sdk_state": state.sdk_state,
                            "created_at": state.created_at.isoformat(),
                            "expires_at": state.expires_at.isoformat(),
                        }
                    ),
                )
                session.add(row)
                for approval in state.approvals:
                    alias = secrets.token_hex(8)
                    session.add(
                        V2PendingApproval(
                            id=alias,
                            chat_id=state.chat_id,
                            agent_run_id=state.run_id,
                            action=approval.tool_name,
                            payload_json=json.dumps(
                                {"call_id": approval.approval_id, "arguments": approval.arguments}
                            ),
                            summary=approval.summary,
                            expires_at=state.expires_at,
                        )
                    )
                session.commit()

        await asyncio.to_thread(execute)

    async def load(self, *, chat_id: int) -> SerializedRunState | None:
        def execute():
            with Session(self.engine) as session:
                run = session.exec(
                    select(V2AgentRun)
                    .where(
                        V2AgentRun.chat_id == chat_id,
                        V2AgentRun.status == AgentRunStatus.PAUSED,
                    )
                    .order_by(V2AgentRun.updated_at.desc())
                ).first()
                if run is None:
                    return None
                envelope = json.loads(run.state_json)
                rows = session.exec(
                    select(V2PendingApproval).where(
                        V2PendingApproval.agent_run_id == run.id,
                        V2PendingApproval.status == ApprovalStatus.PENDING,
                    )
                ).all()
                approvals = []
                for row in rows:
                    payload = json.loads(row.payload_json)
                    approvals.append(
                        PendingApproval(
                            approval_id=str(payload["call_id"]),
                            call_id=str(payload["call_id"]),
                            tool_name=row.action,
                            arguments=payload.get("arguments", {}),
                            summary=row.summary,
                        )
                    )
                return SerializedRunState(
                    chat_id=chat_id,
                    run_id=run.id,
                    sdk_state=envelope["sdk_state"],
                    approvals=approvals,
                    created_at=dt.datetime.fromisoformat(envelope["created_at"]),
                    expires_at=dt.datetime.fromisoformat(envelope["expires_at"]),
                )

        return await asyncio.to_thread(execute)

    async def delete(self, *, chat_id: int, run_id: str) -> None:
        def execute():
            with Session(self.engine) as session:
                run = session.get(V2AgentRun, run_id)
                if run is not None and run.chat_id == chat_id:
                    run.status = AgentRunStatus.COMPLETED
                    run.completed_at = utcnow()
                session.commit()

        await asyncio.to_thread(execute)

    async def abandon(self, *, chat_id: int) -> None:
        """Expire paused runs when a newer user message supersedes them."""

        def execute():
            with Session(self.engine) as session:
                runs = session.exec(
                    select(V2AgentRun).where(
                        V2AgentRun.chat_id == chat_id,
                        V2AgentRun.status == AgentRunStatus.PAUSED,
                    )
                ).all()
                run_ids = [run.id for run in runs]
                for run in runs:
                    run.status = AgentRunStatus.FAILED
                    run.completed_at = utcnow()
                if run_ids:
                    approvals = session.exec(
                        select(V2PendingApproval).where(
                            V2PendingApproval.agent_run_id.in_(run_ids),
                            V2PendingApproval.status == ApprovalStatus.PENDING,
                        )
                    ).all()
                    for approval in approvals:
                        approval.status = ApprovalStatus.EXPIRED
                        approval.resolved_at = utcnow()
                session.commit()

        await asyncio.to_thread(execute)

    async def pending_rows(self, chat_id: int) -> list[V2PendingApproval]:
        def execute():
            with Session(self.engine) as session:
                return list(
                    session.exec(
                        select(V2PendingApproval).where(
                            V2PendingApproval.chat_id == chat_id,
                            V2PendingApproval.status == ApprovalStatus.PENDING,
                            V2PendingApproval.expires_at > utcnow(),
                        )
                    ).all()
                )

        return await asyncio.to_thread(execute)

    async def resolve_alias(self, chat_id: int, alias: str, approved: bool) -> str | None:
        del approved

        def execute():
            with Session(self.engine) as session:
                row = session.get(V2PendingApproval, alias)
                expires_at = row.expires_at if row is not None else None
                if expires_at is not None and expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=dt.UTC)
                if (
                    row is None
                    or row.chat_id != chat_id
                    or row.status != ApprovalStatus.PENDING
                    or expires_at is None
                    or expires_at <= utcnow()
                ):
                    return None
                call_id = str(json.loads(row.payload_json)["call_id"])
                return call_id

        return await asyncio.to_thread(execute)

    async def finalize_alias(self, chat_id: int, alias: str, approved: bool) -> None:
        def execute():
            with Session(self.engine) as session:
                row = session.get(V2PendingApproval, alias)
                if (
                    row is not None
                    and row.chat_id == chat_id
                    and row.status == ApprovalStatus.PENDING
                ):
                    row.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
                    row.resolved_at = utcnow()
                    session.commit()

        await asyncio.to_thread(execute)

    async def set_approval_message(self, alias: str, message_id: int) -> None:
        def execute():
            with Session(self.engine) as session:
                row = session.get(V2PendingApproval, alias)
                if row is not None:
                    row.approval_message_id = message_id
                    session.commit()

        await asyncio.to_thread(execute)


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
