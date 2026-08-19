"""Composition root for Telegram, the Terra runner, and deterministic controls."""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager

import structlog
from sqlmodel import Session, select

from leetcode_coach.agent.advisor import OpenAISolAdvisor
from leetcode_coach.agent.orchestrator import (
    AgentRunOutcome,
    AgentRuntimeContext,
    AgentSettings,
    TerraCoachRunner,
)
from leetcode_coach.clock import local_today
from leetcode_coach.config import get_settings
from leetcode_coach.db.models import (
    AgentRunStatus,
    CandidateStatus,
    ProposalStatus,
    ReviewStatus,
    V2AgentRun,
    V2BotState,
    V2PendingReview,
    V2Problem,
    V2ProposalBatch,
    V2ProposalCandidate,
    as_utc,
    utcnow,
)
from leetcode_coach.domain.exceptions import DomainError
from leetcode_coach.domain.services import CoachDomain
from leetcode_coach.integrations.telegram import answer_callback, edit_message, send_message
from leetcode_coach.rendering import (
    paginate_proposal_html,
    proposal_keyboard,
    render_work_receipt,
)
from leetcode_coach.runtime.adapters import (
    PostgresAgentSession,
    SQLCoachDomainAdapter,
)

log = structlog.get_logger("v2.application")


def configure_openai_sdk(api_key: str) -> None:
    """Give the Agents SDK the key loaded by pydantic-settings.

    The SDK reads process environment variables by default, while V2 also
    supports keys loaded from ``.env``. Keeping those sources synchronized at
    the composition root prevents a configured service from failing its first
    agent turn with a misleading missing-credentials error.
    """
    from agents import set_default_openai_client
    from openai import AsyncOpenAI

    set_default_openai_client(
        AsyncOpenAI(api_key=api_key, timeout=90.0, max_retries=2),
        use_for_tracing=True,
    )


class CoachApplication:
    def __init__(self, engine) -> None:
        self.engine = engine
        self.domain = SQLCoachDomainAdapter(engine)
        settings = get_settings()
        configure_openai_sdk(settings.openai_api_key)
        self.agent_settings = AgentSettings.from_config(settings)
        self.runner = TerraCoachRunner(settings=self.agent_settings)
        self.advisor = OpenAISolAdvisor(
            model=settings.sol_advisor_model, api_key=settings.openai_api_key
        )
        self._locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    def _acquire_database_chat_lock(self, chat_id: int):
        if self.engine.dialect.name != "postgresql":
            return None
        connection = self.engine.raw_connection()
        cursor = connection.cursor()
        cursor.execute("SELECT pg_advisory_lock(%s)", (9_000_000_000 + abs(chat_id),))
        cursor.close()
        return connection

    @staticmethod
    def _release_database_chat_lock(connection, chat_id: int) -> None:
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT pg_advisory_unlock(%s)", (9_000_000_000 + abs(chat_id),))
        finally:
            cursor.close()
            connection.close()

    @asynccontextmanager
    async def _chat_guard(self, chat_id: int):
        async with self._locks[chat_id]:
            connection = await asyncio.to_thread(self._acquire_database_chat_lock, chat_id)
            try:
                yield
            finally:
                if connection is not None:
                    await asyncio.to_thread(self._release_database_chat_lock, connection, chat_id)

    def _context(self, chat_id: int, *, operation_key: str | None = None) -> AgentRuntimeContext:
        return AgentRuntimeContext(
            chat_id=chat_id,
            domain=self.domain,
            sol_advisor=self.advisor,
            operation_key=operation_key,
        )

    async def handle_text(
        self,
        *,
        chat_id: int,
        text: str,
        message_id: int,
        reply_to_message_id: int | None,
    ) -> None:
        async with self._chat_guard(chat_id):
            outcome = await self.runner.run(
                message=text,
                context=self._context(chat_id, operation_key=f"telegram-message-{message_id}"),
                session=PostgresAgentSession(self.engine, chat_id),
            )
            await self._deliver_outcome(chat_id, outcome, reply_to_message_id=message_id)

    async def handle_callback(self, *, chat_id: int, callback_id: str, data: str) -> None:
        async with self._chat_guard(chat_id):
            if data.startswith("v2p:"):
                try:
                    _, batch, position = data.split(":", 2)
                    batch_id, candidate_position = int(batch), int(position)
                except ValueError:
                    await answer_callback(callback_id, "This action is no longer active.")
                    return
                await answer_callback(callback_id)
                try:
                    await self._direct_pick(
                        chat_id, batch_id, candidate_position, callback_id=callback_id
                    )
                except DomainError:
                    await send_message(chat_id, "That proposal is no longer active.")
                return
            if data.startswith("v2pd:"):
                try:
                    _, batch = data.split(":", 1)
                    batch_id = int(batch)
                except ValueError:
                    await answer_callback(callback_id, "This action is no longer active.")
                    return
                await answer_callback(callback_id)
                try:
                    await self._commit_direct_picks(chat_id, batch_id)
                except DomainError:
                    await send_message(chat_id, "That proposal is no longer active.")
                return
            if data.startswith("v2r:"):
                try:
                    _, action, review = data.split(":", 2)
                    review_id = int(review)
                except ValueError:
                    await answer_callback(callback_id, "This action is no longer active.")
                    return
                await answer_callback(callback_id)
                try:
                    await self._direct_review_action(
                        chat_id, action, review_id, callback_id=callback_id
                    )
                except DomainError:
                    await send_message(chat_id, "That review action is no longer active.")
                return
            if data.startswith("v2x:"):
                try:
                    _, batch = data.split(":", 1)
                    batch_id = int(batch)
                except ValueError:
                    await answer_callback(callback_id, "This action is no longer active.")
                    return
                await answer_callback(callback_id)
                try:
                    result = await self.domain.extend_proposal(
                        chat_id=chat_id, batch_id=str(batch_id)
                    )
                except DomainError:
                    await send_message(chat_id, "That proposal is no longer active.")
                    return
                with Session(self.engine) as session:
                    proposal = session.get(V2ProposalBatch, batch_id)
                    candidate_count = len(
                        session.exec(
                            select(V2ProposalCandidate).where(
                                V2ProposalCandidate.batch_id == batch_id
                            )
                        ).all()
                    )
                if proposal is not None and proposal.telegram_message_id is not None:
                    await edit_message(
                        chat_id,
                        proposal.telegram_message_id,
                        reply_markup=proposal_keyboard(proposal.id, candidate_count),
                    )
                await send_message(chat_id, f"Extended proposal {result['id']} for 24 hours.")
                return
            if data == "v2n:accept":
                await answer_callback(callback_id)
                await self.domain.accept_credit_deficit(
                    chat_id=chat_id, date=local_today().isoformat()
                )
                await send_message(chat_id, "Deficit acknowledged.")
                return
            if data == "v2n:solve":
                await answer_callback(callback_id)
                queue = await self.domain.get_open_queue(chat_id=chat_id)
                reviews = queue.get("reviews", [])
                lines: list[str] = []
                for review in reviews:
                    problem = await self.domain.get_problem(
                        chat_id=chat_id, slug=str(review.get("problem_slug", ""))
                    )
                    if problem is not None:
                        lines.append(f"- {problem['title']}")
                await send_message(
                    chat_id,
                    "Open queue:\n" + "\n".join(lines) if lines else "Open queue is empty.",
                )
                return
            if data == "v2n:snooze":
                await answer_callback(callback_id)
                with Session(self.engine) as session:
                    CoachDomain(session).set_bot_state(
                        chat_id, "nudge_snoozed_on", local_today().isoformat()
                    )
                    session.commit()
                await send_message(chat_id, "Nudge snoozed until tomorrow.")
                return
            await answer_callback(callback_id, "This action is no longer active.")

    async def _deliver_outcome(
        self, chat_id: int, outcome: AgentRunOutcome, *, reply_to_message_id: int | None
    ) -> None:
        log.info("agent_run", status=outcome.status, **outcome.metrics)
        await asyncio.to_thread(self._persist_metrics, chat_id, outcome)
        for receipt in outcome.receipts:
            await send_message(
                chat_id,
                render_work_receipt(receipt),
                reply_to_message_id=reply_to_message_id,
            )
        await self._send_unsent_proposal(chat_id)
        await self._send_unsent_reviews(chat_id)
        if outcome.text:
            await send_message(
                chat_id,
                outcome.text,
                reply_to_message_id=reply_to_message_id,
            )

    def _persist_metrics(self, chat_id: int, outcome: AgentRunOutcome) -> None:
        metrics = outcome.metrics
        started_raw = metrics.get("started_at")
        finished_raw = metrics.get("finished_at")
        started = dt.datetime.fromisoformat(started_raw) if started_raw else dt.datetime.now(dt.UTC)
        finished = dt.datetime.fromisoformat(finished_raw) if finished_raw else None
        latency_ms = int((finished - started).total_seconds() * 1000) if finished else None
        with Session(self.engine) as session:
            session.add(
                V2AgentRun(
                    id=f"metrics-{uuid.uuid4().hex}",
                    chat_id=chat_id,
                    status=AgentRunStatus.COMPLETED,
                    turn_count=int(metrics.get("turns") or 0),
                    sol_calls=int(metrics.get("sol_escalations") or 0),
                    model=str(metrics.get("model") or self.agent_settings.terra_model),
                    input_tokens=int(metrics.get("input_tokens") or 0),
                    output_tokens=int(metrics.get("output_tokens") or 0),
                    cache_read_tokens=int(metrics.get("cached_tokens") or 0),
                    cache_write_tokens=int(metrics.get("cache_write_tokens") or 0),
                    tool_calls=int(metrics.get("tool_calls") or 0),
                    escalation_reason=(
                        str(metrics["escalation_reason"])
                        if metrics.get("escalation_reason")
                        else None
                    ),
                    latency_ms=latency_ms,
                    started_at=started,
                    completed_at=finished if outcome.status == "completed" else None,
                )
            )
            session.commit()

    async def _send_unsent_proposal(self, chat_id: int) -> bool:
        def load():
            with Session(self.engine) as session:
                batch = session.exec(
                    select(V2ProposalBatch)
                    .where(
                        V2ProposalBatch.chat_id == chat_id,
                        V2ProposalBatch.telegram_message_id == None,  # noqa: E711
                        V2ProposalBatch.status == ProposalStatus.OPEN,
                    )
                    .order_by(V2ProposalBatch.id.desc())
                ).first()
                if batch is None:
                    return None
                if batch.status != ProposalStatus.OPEN:
                    progress = session.exec(
                        select(V2BotState).where(
                            V2BotState.chat_id == chat_id,
                            V2BotState.key == f"proposal_delivery:{batch.id}",
                        )
                    ).first()
                    if progress is not None:
                        session.delete(progress)
                        session.commit()
                    return None
                candidates = session.exec(
                    select(V2ProposalCandidate)
                    .where(V2ProposalCandidate.batch_id == batch.id)
                    .order_by(V2ProposalCandidate.position)
                ).all()
                cards = []
                for candidate in candidates:
                    problem = session.get(V2Problem, candidate.problem_slug)
                    if problem is None:
                        continue
                    cards.append(
                        {
                            **problem.model_dump(mode="json", warnings=False),
                            "reasoning": candidate.reasoning,
                            "coaching_hint": candidate.coaching_hint,
                        }
                    )
                delivery = session.exec(
                    select(V2BotState).where(
                        V2BotState.chat_id == chat_id,
                        V2BotState.key == f"proposal_delivery:{batch.id}",
                    )
                ).first()
                progress = {"next_page": 0, "first_message_id": None}
                if delivery is not None:
                    try:
                        parsed = json.loads(delivery.value)
                        if isinstance(parsed, dict):
                            progress["next_page"] = max(0, int(parsed.get("next_page", 0)))
                            first = parsed.get("first_message_id")
                            progress["first_message_id"] = int(first) if first is not None else None
                    except (TypeError, ValueError, json.JSONDecodeError):
                        pass
                return batch.id, cards, progress

        loaded = await asyncio.to_thread(load)
        if loaded is None:
            return False
        batch_id, cards, progress = loaded
        pages = paginate_proposal_html(cards)
        if not pages:
            return False
        next_page = min(int(progress["next_page"]), len(pages))
        first_message_id = progress["first_message_id"]
        for page_index in range(next_page, len(pages)):
            text, _positions = pages[page_index]
            message_id = await send_message(
                chat_id,
                text,
                parse_mode="HTML",
            )
            if first_message_id is None:
                first_message_id = message_id

            def save_progress(
                saved_page: int = page_index + 1,
                saved_first: int | None = first_message_id,
            ) -> None:
                with Session(self.engine) as session:
                    CoachDomain(session).set_bot_state(
                        chat_id,
                        f"proposal_delivery:{batch_id}",
                        json.dumps(
                            {
                                "next_page": saved_page,
                                "first_message_id": saved_first,
                            },
                            separators=(",", ":"),
                        ),
                    )
                    session.commit()

            await asyncio.to_thread(save_progress)

        controller_message_id = await send_message(
            chat_id,
            "Choose any problems, then tap Done.",
            reply_markup=proposal_keyboard(batch_id, len(cards)),
        )

        def persist():
            with Session(self.engine) as session:
                batch = session.get(V2ProposalBatch, batch_id)
                if batch is not None and batch.status == ProposalStatus.OPEN:
                    batch.telegram_message_id = controller_message_id
                    delivery = session.exec(
                        select(V2BotState).where(
                            V2BotState.chat_id == chat_id,
                            V2BotState.key == f"proposal_delivery:{batch_id}",
                        )
                    ).first()
                    if delivery is not None:
                        session.delete(delivery)
                    session.commit()

        await asyncio.to_thread(persist)
        return True

    async def _send_unsent_reviews(self, chat_id: int) -> None:
        def load_review_ids() -> list[int]:
            with Session(self.engine) as session:
                return [
                    review.id
                    for review in session.exec(
                        select(V2PendingReview).where(
                            V2PendingReview.chat_id == chat_id,
                            V2PendingReview.status == ReviewStatus.OPEN,
                            V2PendingReview.telegram_message_id == None,  # noqa: E711
                        )
                    ).all()
                    if review.id is not None
                ]

        review_ids = await asyncio.to_thread(load_review_ids)
        if review_ids:
            await self._send_review_threads(chat_id, review_ids)

    async def _direct_pick(
        self, chat_id: int, batch_id: int, position: int, *, callback_id: str
    ) -> None:
        key = f"pick:{batch_id}"
        callback_key = (
            f"pickcb:{batch_id}:" f"{hashlib.sha256(callback_id.encode('utf-8')).hexdigest()[:24]}"
        )

        def choose():
            with Session(self.engine) as session:
                batch = session.get(V2ProposalBatch, batch_id)
                if (
                    batch is None
                    or batch.chat_id != chat_id
                    or batch.status != ProposalStatus.OPEN
                    or as_utc(batch.expires_at) <= utcnow()
                ):
                    return None
                candidate = session.exec(
                    select(V2ProposalCandidate).where(
                        V2ProposalCandidate.batch_id == batch_id,
                        V2ProposalCandidate.position == position,
                    )
                ).first()
                if candidate is None or candidate.status != CandidateStatus.AVAILABLE:
                    return None
                replay = session.exec(
                    select(V2BotState).where(
                        V2BotState.chat_id == chat_id, V2BotState.key == callback_key
                    )
                ).first()
                if replay is not None:
                    parsed = json.loads(replay.value)
                    return parsed["action"], parsed["slug"], parsed["count"]
                state = session.exec(
                    select(V2BotState).where(V2BotState.chat_id == chat_id, V2BotState.key == key)
                ).first()
                selected = json.loads(state.value) if state is not None else []
                if not isinstance(selected, list) or any(
                    not isinstance(item, str) for item in selected
                ):
                    selected = []
                if candidate.problem_slug in selected:
                    selected.remove(candidate.problem_slug)
                    action = "Unselected"
                else:
                    selected.append(candidate.problem_slug)
                    action = "Selected"
                if selected:
                    CoachDomain(session).set_bot_state(
                        chat_id, key, json.dumps(selected, separators=(",", ":"))
                    )
                elif state is not None:
                    session.delete(state)
                CoachDomain(session).set_bot_state(
                    chat_id,
                    callback_key,
                    json.dumps(
                        {"action": action, "slug": candidate.problem_slug, "count": len(selected)},
                        separators=(",", ":"),
                    ),
                )
                session.commit()
                return action, candidate.problem_slug, len(selected)

        result = await asyncio.to_thread(choose)
        if result is None:
            await send_message(chat_id, "That proposal is no longer active.")
        else:
            await send_message(
                chat_id,
                f"{result[0]} {result[1]}. {result[2]} selected; tap Done when ready.",
            )

    async def _commit_direct_picks(self, chat_id: int, batch_id: int) -> None:
        key = f"pick:{batch_id}"

        def commit():
            with Session(self.engine) as session:
                batch = session.get(V2ProposalBatch, batch_id)
                if (
                    batch is not None
                    and batch.chat_id == chat_id
                    and batch.status == ProposalStatus.PICKED
                ):
                    return [
                        row.id
                        for row in session.exec(
                            select(V2PendingReview).where(
                                V2PendingReview.chat_id == chat_id,
                                V2PendingReview.batch_id == batch_id,
                            )
                        ).all()
                    ]
                state = session.exec(
                    select(V2BotState).where(V2BotState.chat_id == chat_id, V2BotState.key == key)
                ).first()
                if (
                    batch is None
                    or batch.chat_id != chat_id
                    or batch.status != ProposalStatus.OPEN
                    or as_utc(batch.expires_at) <= utcnow()
                    or state is None
                ):
                    return None
                selected = json.loads(state.value)
                if not isinstance(selected, list) or not selected:
                    return None
                reviews = CoachDomain(session).commit_picks(chat_id, batch_id, selected)
                session.delete(state)
                cleanup = session.exec(
                    select(V2BotState).where(
                        V2BotState.chat_id == chat_id,
                        V2BotState.key.startswith(f"pickcb:{batch_id}:"),
                    )
                ).all()
                delivery = session.exec(
                    select(V2BotState).where(
                        V2BotState.chat_id == chat_id,
                        V2BotState.key == f"proposal_delivery:{batch_id}",
                    )
                ).first()
                for row in cleanup:
                    session.delete(row)
                if delivery is not None:
                    session.delete(delivery)
                session.commit()
                return [review.id for review in reviews]

        review_ids = await asyncio.to_thread(commit)
        if review_ids is None:
            raise DomainError("proposal selection is unavailable")
        await self._send_review_threads(chat_id, review_ids)

    async def _send_review_threads(self, chat_id: int, review_ids: list[int]) -> None:
        for review_id in review_ids:
            with Session(self.engine) as session:
                review = session.get(V2PendingReview, review_id)
                problem = session.get(V2Problem, review.problem_slug) if review else None
            if review is None or problem is None or review.telegram_message_id is not None:
                continue
            message_id = await send_message(
                chat_id,
                f"{problem.title}\n{problem.url}\nReply with your code when ready.",
                reply_markup={
                    "inline_keyboard": [
                        [
                            {"text": "Skip", "callback_data": f"v2r:skip:{review.id}"},
                            {"text": "Hint", "callback_data": f"v2r:hint:{review.id}"},
                            {"text": "Saw solution", "callback_data": f"v2r:solution:{review.id}"},
                            {"text": "Why", "callback_data": f"v2r:why:{review.id}"},
                        ]
                    ]
                },
            )
            with Session(self.engine) as session:
                current = session.get(V2PendingReview, review_id)
                if current is not None:
                    current.telegram_message_id = message_id
                    session.commit()

    async def _direct_review_action(
        self, chat_id: int, action: str, review_id: int, *, callback_id: str | None = None
    ) -> None:
        if action == "skip":
            await self.domain.skip_problem(chat_id=chat_id, review_id=str(review_id))
            await send_message(chat_id, "Skipped.")
        elif action == "solution":
            await self.domain.mark_solution_viewed(chat_id=chat_id, review_id=str(review_id))
            await send_message(chat_id, "Recorded as saw solution.")
        elif action == "reattempt":
            result = await self.domain.reattempt_problem(chat_id=chat_id, review_id=str(review_id))
            await self._send_review_threads(chat_id, [int(result["id"])])
        elif action in {"hint", "why"}:
            callback_key = f"review_coaching:{review_id}:{callback_id or action}"
            with Session(self.engine) as session:
                review = session.get(V2PendingReview, review_id)
                replay = session.exec(
                    select(V2BotState).where(
                        V2BotState.chat_id == chat_id, V2BotState.key == callback_key
                    )
                ).first()
                candidate = (
                    session.get(V2ProposalCandidate, review.candidate_id)
                    if review is not None
                    and review.chat_id == chat_id
                    and review.candidate_id is not None
                    else None
                )
                problem = (
                    session.get(V2Problem, review.problem_slug)
                    if review is not None and review.chat_id == chat_id
                    else None
                )
                level_state = session.exec(
                    select(V2BotState).where(
                        V2BotState.chat_id == chat_id,
                        V2BotState.key == f"hint_level:{review_id}",
                    )
                ).first()
                hint_level = min(3, int(level_state.value) + 1) if level_state else 1
            if replay is not None:
                await send_message(chat_id, replay.value)
                return
            if review is None or problem is None:
                await send_message(chat_id, "That review action is no longer active.")
            else:
                optional_hint = candidate.coaching_hint if candidate is not None else ""
                optional_reason = candidate.reasoning if candidate is not None else ""
                coaching_request = (
                    f"Give hint level {hint_level} of 3 for {problem.title} ({problem.slug}). "
                    f"Level 1 is conceptual only; level 2 states the invariant and next step; "
                    f"level 3 gives pseudocode without a full solution. Optional prior context: "
                    f"{optional_hint}"
                    if action == "hint"
                    else f"Explain concisely why working on {problem.title} ({problem.slug}) is "
                    f"useful for me now. Optional selection rationale: {optional_reason}"
                )
                outcome = await self.runner.run(
                    message=coaching_request,
                    context=self._context(
                        chat_id, operation_key=f"telegram-callback-{callback_id}"
                    ),
                    session=PostgresAgentSession(self.engine, chat_id),
                )
                if outcome.text:
                    with Session(self.engine) as session:
                        domain = CoachDomain(session)
                        domain.set_bot_state(chat_id, callback_key, outcome.text[:4000])
                        if action == "hint":
                            domain.set_bot_state(
                                chat_id, f"hint_level:{review_id}", str(hint_level)
                            )
                        session.commit()
                await self._deliver_outcome(
                    chat_id, outcome, reply_to_message_id=review.telegram_message_id
                )
        else:
            await send_message(chat_id, "That review action is no longer active.")
