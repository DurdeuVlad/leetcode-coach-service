"""Live acceptance harness for the real Terra agent and V2 application boundary.

This intentionally calls OpenAI, replaces only Telegram transport, and refuses
to run against anything except an explicitly named local SQLite proof database.
Run Alembic first, then set ``PROOF_DATABASE_URL``.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import sys
from contextlib import suppress
from decimal import Decimal

import httpx
from sqlmodel import Session, func, select

from leetcode_coach import application as application_module
from leetcode_coach import jobs as jobs_module
from leetcode_coach.application import CoachApplication
from leetcode_coach.config import get_settings
from leetcode_coach.db.base import create_db_engine
from leetcode_coach.db.models import (
    ApprovalStatus,
    Difficulty,
    ProposalStatus,
    ReviewStatus,
    V2AgentRun,
    V2Attempt,
    V2CreditLedger,
    V2Lesson,
    V2PendingApproval,
    V2PendingReview,
    V2Problem,
    V2ProcessedUpdate,
    V2ProposalBatch,
    V2ProposalCandidate,
    utcnow,
)
from leetcode_coach.domain.schemas import ProposalSelection
from leetcode_coach.domain.services import CoachDomain
from leetcode_coach.integrations.leetcode import ProblemRecord


class Transcript:
    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.edits: list[dict] = []
        self.next_message_id = 10_000

    async def send(self, chat_id, text, **kwargs) -> int:
        message_id = self.next_message_id
        self.next_message_id += 1
        record = {"id": message_id, "chat_id": int(chat_id), "text": text, **kwargs}
        self.messages.append(record)
        print(f"BOT[{message_id}] {text[:700]}")
        if kwargs.get("reply_markup"):
            print("BUTTONS", json.dumps(kwargs["reply_markup"], ensure_ascii=False))
        return message_id

    async def edit(self, chat_id, message_id, **kwargs) -> None:
        self.edits.append({"chat_id": int(chat_id), "message_id": message_id, **kwargs})
        print(f"EDIT[{message_id}]", json.dumps(kwargs, ensure_ascii=False))

    async def acknowledge(self, callback_id, text=None) -> None:
        print(f"ACK[{callback_id}] {text or 'ok'}")


def _count(session: Session, model) -> int:
    return int(session.exec(select(func.count()).select_from(model)).one())


def _seed(engine) -> None:
    problems = [
        ("number-of-islands", "Number of Islands", Difficulty.MEDIUM, "array,dfs,bfs,matrix"),
        ("merge-intervals", "Merge Intervals", Difficulty.MEDIUM, "array,sorting"),
        ("decode-ways", "Decode Ways", Difficulty.MEDIUM, "string,dynamic programming"),
        (
            "longest-consecutive-sequence",
            "Longest Consecutive Sequence",
            Difficulty.HARD,
            "array,hash table",
        ),
        (
            "trapping-rain-water",
            "Trapping Rain Water",
            Difficulty.HARD,
            "array,two pointers,stack",
        ),
        ("house-robber", "House Robber", Difficulty.EASY, "array,dynamic programming"),
        ("course-schedule", "Course Schedule", Difficulty.MEDIUM, "graph,topological-sort"),
        ("coin-change", "Coin Change", Difficulty.MEDIUM, "array,dynamic-programming"),
        (
            "minimum-window-substring",
            "Minimum Window Substring",
            Difficulty.HARD,
            "string,sliding-window",
        ),
        ("word-ladder-ii", "Word Ladder II", Difficulty.HARD, "bfs,backtracking"),
    ]
    with Session(engine) as session:
        if _count(session, V2Problem) or _count(session, V2AgentRun):
            raise RuntimeError("proof database must be freshly migrated and empty")
        for slug, title, difficulty, tags in problems:
            session.add(
                V2Problem(
                    slug=slug,
                    title=title,
                    url=f"https://leetcode.com/problems/{slug}/",
                    difficulty=difficulty,
                    tags=tags,
                )
            )
        session.commit()


async def _prompt(app: CoachApplication, chat_id: int, message_id: int, text: str) -> None:
    print(f"\nUSER[{message_id}] {text[:900]}")
    await app.handle_text(
        chat_id=chat_id,
        text=text,
        message_id=message_id,
        reply_to_message_id=None,
    )


def _pending(engine) -> list[V2PendingApproval]:
    with Session(engine) as session:
        return list(
            session.exec(
                select(V2PendingApproval).where(V2PendingApproval.status == ApprovalStatus.PENDING)
            ).all()
        )


async def _approve_one_after_restart(engine, chat_id: int) -> None:
    rows = _pending(engine)
    assert len(rows) == 1, [(row.action, row.summary) for row in rows]
    row = rows[0]
    assert row.approval_message_id is not None
    restarted = CoachApplication(engine)
    print(f"\nRESTART + USER reply-to {row.approval_message_id}: yes")
    await restarted.handle_text(
        chat_id=chat_id,
        text="yes",
        message_id=90_000 + row.approval_message_id,
        reply_to_message_id=row.approval_message_id,
    )
    assert not _pending(engine)


async def _run() -> None:
    for stream in (sys.stdout, sys.stderr):
        with suppress(AttributeError, OSError):
            stream.reconfigure(encoding="utf-8", errors="replace")
    database_url = os.environ.get("PROOF_DATABASE_URL", "")
    local_sqlite = database_url.startswith("sqlite:///") and (
        ".local-live-proof.db" in database_url
    )
    local_postgres = (
        os.environ.get("PROOF_ALLOW_POSTGRES") == "1"
        and database_url == "postgresql+psycopg://proof:proof@127.0.0.1:55432/proof"
    )
    if not local_sqlite and not local_postgres:
        raise SystemExit(
            "PROOF_DATABASE_URL must target .local-live-proof.db or the explicitly "
            "enabled localhost PostgreSQL proof DSN"
        )
    engine = create_db_engine(database_url)
    _seed(engine)
    transcript = Transcript()
    application_module.send_message = transcript.send
    application_module.edit_message = transcript.edit
    application_module.answer_callback = transcript.acknowledge
    jobs_module.send_message = transcript.send
    jobs_module.edit_message = transcript.edit
    jobs_module.engine = engine

    chat_id = int(get_settings().telegram_chat_id)
    app = CoachApplication(engine)
    await _prompt(
        app,
        chat_id,
        1,
        "Build today's five-problem proposal now. Use only the canonical eligible "
        "unsolved pool. Pick exactly three medium and two hard problems, including "
        "Number of Islands and Decode Ways, with concise reasons and one hint each.",
    )
    with Session(engine) as session:
        batch = session.exec(select(V2ProposalBatch)).one()
        candidates = session.exec(
            select(V2ProposalCandidate)
            .where(V2ProposalCandidate.batch_id == batch.id)
            .order_by(V2ProposalCandidate.position)
        ).all()
        assert len(candidates) == 5
        slugs = [candidate.problem_slug for candidate in candidates]
        assert "number-of-islands" in slugs and "decode-ways" in slugs
        assert "house-robber" not in slugs
        assert batch.telegram_message_id is not None
    proposal_message = transcript.messages[-1]
    assert proposal_message["parse_mode"] == "HTML"
    assert "\\." not in proposal_message["text"] and "\\-" not in proposal_message["text"]

    await _prompt(
        app,
        chat_id,
        2,
        "Give me a compact status report with queue, attempts, active lessons, streak, "
        "and credits. Do not change anything.",
    )
    assert "0.00" in transcript.messages[-1]["text"]

    await _prompt(
        app,
        chat_id,
        3,
        "I choose Number of Islands and Decode Ways from today's proposal. Commit both picks.",
    )
    assert _pending(engine)[0].action == "commit_picks"
    await _approve_one_after_restart(engine, chat_id)
    with Session(engine) as session:
        reviews = session.exec(select(V2PendingReview).order_by(V2PendingReview.id)).all()
        assert len(reviews) == 2 and all(review.telegram_message_id for review in reviews)
        island_review = next(row for row in reviews if row.problem_slug == "number-of-islands")
        decode_review = next(row for row in reviews if row.problem_slug == "decode-ways")

    await app.handle_callback(
        chat_id=chat_id, callback_id="hint", data=f"v2r:hint:{island_review.id}"
    )
    await app.handle_callback(
        chat_id=chat_id, callback_id="why", data=f"v2r:why:{island_review.id}"
    )
    java = """class Solution {
  public int numIslands(char[][] grid) {
    int count = 0;
    for (int r = 0; r < grid.length; r++)
      for (int c = 0; c < grid[0].length; c++)
        if (grid[r][c] == '1') { count++; dfs(grid, r, c); }
    return count;
  }
  void dfs(char[][] g, int r, int c) {
    if (r < 0 || c < 0 || r == g.length || c == g[0].length || g[r][c] != '1') return;
    g[r][c] = '0';
    dfs(g,r+1,c); dfs(g,r-1,c); dfs(g,r,c+1); dfs(g,r,c-1);
  }
}"""
    await _prompt(
        app,
        chat_id,
        4,
        "This Java solution for Number of Islands passed LeetCode. Review correctness, "
        "invariant, complexity, and risks, then record its open review as solved with "
        f"useful feedback and a new lesson if warranted.\n\n{java}",
    )
    assert _pending(engine)[0].action == "commit_attempt"
    await _approve_one_after_restart(engine, chat_id)
    with Session(engine) as session:
        assert _count(session, V2Attempt) == 1
        assert _count(session, V2Lesson) == 1
        assert CoachDomain(session).credit_balance(chat_id) == Decimal("1.00")

    await app.handle_callback(
        chat_id=chat_id, callback_id="skip", data=f"v2r:skip:{decode_review.id}"
    )
    await app.handle_callback(
        chat_id=chat_id, callback_id="reattempt", data=f"v2r:reattempt:{decode_review.id}"
    )
    with Session(engine) as session:
        reattempt = session.exec(
            select(V2PendingReview)
            .where(V2PendingReview.problem_slug == "decode-ways")
            .order_by(V2PendingReview.id.desc())
        ).first()
        assert reattempt is not None and reattempt.status == ReviewStatus.OPEN
    await app.handle_callback(
        chat_id=chat_id,
        callback_id="solution",
        data=f"v2r:solution:{reattempt.id}",
    )

    lessons_before: int
    with Session(engine) as session:
        lessons_before = _count(session, V2Lesson)
    await _prompt(
        app,
        chat_id,
        5,
        "Create a new active lesson titled 'Rejected proof lesson' in category testing. "
        "Use adjust_lesson, but wait for my approval.",
    )
    rejection = _pending(engine)[0]
    await app.handle_callback(
        chat_id=chat_id,
        callback_id="reject",
        data=f"v2a:{rejection.id}:no",
    )
    with Session(engine) as session:
        assert _count(session, V2Lesson) == lessons_before

    await _prompt(
        app,
        chat_id,
        6,
        "Explicitly consult Sol once for read-only guidance on recursion-depth risk in the "
        "Java Number of Islands solution. Verify the advice and do not mutate anything.",
    )
    with Session(engine) as session:
        latest_run = session.exec(select(V2AgentRun).order_by(V2AgentRun.started_at.desc())).first()
        assert latest_run is not None and latest_run.sol_calls == 1
        assert latest_run.escalation_reason

    await _prompt(
        app,
        chat_id,
        7,
        "Look up House Robber canonically and state its difficulty. Do not mutate anything.",
    )
    assert "easy" in transcript.messages[-1]["text"].lower()

    with Session(engine) as session:
        expiring_batch, _ = CoachDomain(session).create_proposal(
            chat_id, [ProposalSelection("house-robber", "proof", "proof")]
        )
        expiring_batch.telegram_message_id = 55_555
        expiring_batch.expires_at = utcnow() - dt.timedelta(minutes=1)
        session.commit()
        extension_batch_id = expiring_batch.id
    await jobs_module.expire_state()
    with Session(engine) as session:
        expired = session.get(V2ProposalBatch, extension_batch_id)
        assert expired is not None and expired.status == ProposalStatus.EXPIRED
    assert any(
        f"v2x:{extension_batch_id}" in json.dumps(edit.get("reply_markup"))
        for edit in transcript.edits
    )
    await app.handle_callback(
        chat_id=chat_id,
        callback_id="extend",
        data=f"v2x:{extension_batch_id}",
    )
    with Session(engine) as session:
        extended = session.get(V2ProposalBatch, extension_batch_id)
        assert extended is not None and extended.status == ProposalStatus.OPEN

    batches_before_refill: int
    with Session(engine) as session:
        batches_before_refill = _count(session, V2ProposalBatch)
    await jobs_module.queue_refill()
    with Session(engine) as session:
        assert _count(session, V2ProposalBatch) == batches_before_refill + 1
        refill = session.exec(select(V2ProposalBatch).order_by(V2ProposalBatch.id.desc())).first()
        assert refill is not None and refill.telegram_message_id is not None
        refill_id = refill.id
    await app.handle_callback(chat_id=chat_id, callback_id="pick-one", data=f"v2p:{refill_id}:1")
    await app.handle_callback(chat_id=chat_id, callback_id="pick-two", data=f"v2p:{refill_id}:2")

    await jobs_module.apply_daily_tax()
    await jobs_module.apply_daily_tax()
    with Session(engine) as session:
        domain = CoachDomain(session)
        assert (
            len(
                session.exec(
                    select(V2CreditLedger).where(V2CreditLedger.reason == "daily_tax")
                ).all()
            )
            == 1
        )
        domain.add_credit(chat_id, Decimal("-2.00"), "proof_debt", "proof:debt")
        session.commit()
    await jobs_module.send_nudge()
    assert any("behind" in message["text"].lower() for message in transcript.messages)
    await app.handle_callback(chat_id=chat_id, callback_id="deficit", data="v2n:accept")

    async def fake_recent_solved() -> list[ProblemRecord]:
        return [
            ProblemRecord(
                slug="merge-intervals",
                title="Merge Intervals",
                difficulty="medium",
                tags="array,sorting",
            )
        ]

    jobs_module.fetch_recent_solved = fake_recent_solved
    await jobs_module.refresh_problem_pool()
    with Session(engine) as session:
        refreshed = session.get(V2Problem, "merge-intervals")
        assert refreshed is not None and refreshed.solved is True

    from leetcode_coach import main as main_module

    headers = {}
    if get_settings().telegram_webhook_secret:
        headers["X-Telegram-Bot-Api-Secret-Token"] = get_settings().telegram_webhook_secret
    transport = httpx.ASGITransport(app=main_module.app)
    webhook_payload = {
        "update_id": 880,
        "message": {
            "message_id": 880,
            "chat": {"id": chat_id},
            "text": "Report my current queue and credits. Do not mutate anything.",
        },
    }
    with Session(engine) as session:
        webhook_runs_before = _count(session, V2AgentRun)
    async with httpx.AsyncClient(transport=transport, base_url="http://proof") as client:
        first = await client.post("/telegram/webhook", json=webhook_payload, headers=headers)
        duplicate = await client.post("/telegram/webhook", json=webhook_payload, headers=headers)
        assert first.status_code == duplicate.status_code == 200
    with Session(engine) as session:
        assert _count(session, V2AgentRun) == webhook_runs_before + 1

    original_send = application_module.send_message
    failed_once = False

    async def fail_delivery_once(chat_id, text, **kwargs):
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            raise RuntimeError("simulated Telegram delivery failure")
        return await transcript.send(chat_id, text, **kwargs)

    application_module.send_message = fail_delivery_once
    retry_payload = {
        "update_id": 881,
        "message": {
            "message_id": 881,
            "chat": {"id": chat_id},
            "text": "State my credit balance only. Do not mutate anything.",
        },
    }
    async with httpx.AsyncClient(transport=transport, base_url="http://proof") as client:
        failed = await client.post("/telegram/webhook", json=retry_payload, headers=headers)
        assert failed.status_code == 503
        application_module.send_message = original_send
        retried = await client.post("/telegram/webhook", json=retry_payload, headers=headers)
        assert retried.status_code == 200

    with Session(engine) as session:
        domain = CoachDomain(session)
        assert domain.record_update(777, chat_id) is True
        domain.mark_update_handled(777, "temporary failure")
        assert domain.record_update(777, chat_id) is True
        domain.mark_update_handled(777)
        assert domain.record_update(777, chat_id) is False
        session.commit()
        assert _count(session, V2ProcessedUpdate) == 3
        assert _count(session, V2CreditLedger) >= 4
        runs = session.exec(select(V2AgentRun)).all()
        assert runs and all(run.turn_count <= 8 for run in runs)
        assert any(run.cache_read_tokens > 0 for run in runs)
        assert all(run.tool_calls > 0 for run in runs)

    print(
        "\nLIVE_PROOF_OK",
        json.dumps(
            {
                "messages": len(transcript.messages),
                "edits": len(transcript.edits),
                "agent_runs": len(runs),
                "attempts": 1,
                "lessons": lessons_before,
            },
            sort_keys=True,
        ),
    )


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
