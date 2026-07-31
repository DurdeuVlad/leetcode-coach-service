"""Admin API — API-key-protected trigger endpoints for automated testing.

These endpoints exist so an external AI (or CI script) can exercise the full
Flow A → Flow B pipeline end-to-end over HTTP, without relying on cron jobs or
Telegram interactions. They call the same flow internals as the production
paths but with ``dry_run=True``: Telegram sends are skipped, but all DB writes
and LLM calls still happen — so the test proves the real pipeline works.

Security:
- All endpoints require ``X-Admin-Api-Key`` header matching ``ADMIN_API_KEY``.
- If ``ADMIN_API_KEY`` is blank, the router is not mounted (returns 404) —
  admin endpoints are disabled by default.
- Mismatched key → 401.

Endpoints:
- ``POST /admin/propose`` — run Flow A (5-candidate proposal), return markdown
  + the 5 candidates as JSON.
- ``POST /admin/pick`` — run Flow B pick-parse path, return the created
  pending_review threads.
- ``POST /admin/coach`` — run Flow B coach pass for a submission, return the
  coach feedback + lesson outcome.
"""

from __future__ import annotations

import datetime

import structlog
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from leetcode_coach.config import get_settings
from leetcode_coach.db.base import get_session
from leetcode_coach.db.models import DailyCandidate, PendingReview
from leetcode_coach.flows.flow_a import propose_5
from leetcode_coach.flows.flow_b import _coach_pass_path, _pick_parse_path

log = structlog.get_logger("admin")

router = APIRouter(prefix="/admin", tags=["admin"])


# --- Auth dependency (checked per-request via the settings singleton) ---


def _check_admin_key(x_admin_api_key: str | None) -> None:
    """Validate the X-Admin-Api-Key header against ADMIN_API_KEY.

    The router is only mounted when ADMIN_API_KEY is non-empty (see main.py),
    so reaching this function means the admin API is enabled. A missing or
    mismatched key → 401.
    """
    expected = get_settings().admin_api_key
    if not expected:
        # Should not happen (router not mounted), but defend in depth.
        raise HTTPException(status_code=404, detail="admin API disabled")
    if x_admin_api_key is None or x_admin_api_key != expected:
        raise HTTPException(status_code=401, detail="invalid admin API key")


# --- Request / response models ---


class PickRequest(BaseModel):
    """Pick-parse request: 1-2 integers (1-based indices into the 5-list)."""

    picks: list[int]


class CoachRequest(BaseModel):
    """Coach pass request.

    Either ``pending_review_id`` or ``problem_slug`` identifies the target
    pending_review row. If both are given, ``pending_review_id`` wins.
    ``code`` is the user's submission text (or a status note like "skipped").
    """

    code: str
    pending_review_id: int | None = None
    problem_slug: str | None = None


class CandidateOut(BaseModel):
    pick_index: int
    slug: str
    title: str
    difficulty: str
    reasoning: str
    coaching_hint: str


class ProposeResponse(BaseModel):
    markdown: str
    candidates: list[CandidateOut]


class PickThreadOut(BaseModel):
    pick_index: int
    problem_slug: str
    problem_title: str
    difficulty: str
    message_id: int
    pending_review_id: int


class PickResponse(BaseModel):
    picked: list[PickThreadOut]


class CoachResponse(BaseModel):
    tutor_feedback: str
    lesson_title: str
    lesson_category: str
    lesson_is_recurring: bool
    lesson_should_graduate: bool
    solved: bool
    status: str
    next_step: str
    time_spent_min: int | None
    lesson_action: str
    lesson_title_outcome: str
    times_reinforced: int
    reply_text: str


# --- Endpoints ---


@router.post("/propose")
async def admin_propose(
    x_admin_api_key: str | None = Header(default=None),
) -> ProposeResponse:
    """Trigger Flow A (the 5-candidate proposal) without sending to Telegram.

    Persists the 5 candidates to ``daily_candidates`` so a subsequent
    ``POST /admin/pick`` can reference them. Returns the markdown + the
    parsed candidates as JSON.
    """
    _check_admin_key(x_admin_api_key)
    log.info("admin_propose_start")

    markdown = await propose_5(dry_run=True)

    # Query the just-persisted candidates to return structured data.
    today = datetime.date.today()
    with next(get_session()) as session:
        rows = session.exec(
            select(DailyCandidate)
            .where(DailyCandidate.proposed_at == today)
            .order_by(DailyCandidate.pick_index)
        ).all()

    candidates = [
        CandidateOut(
            pick_index=r.pick_index,
            slug=r.slug,
            title=r.title,
            difficulty=r.difficulty,
            reasoning=r.reasoning,
            coaching_hint=r.coaching_hint,
        )
        for r in rows
    ]
    log.info("admin_propose_done", candidate_count=len(candidates))

    # FR-8.2: refresh pinned message after admin-driven Flow A.
    try:
        from leetcode_coach.flows.pinned import refresh_pinned_message

        await refresh_pinned_message()
    except Exception:
        log.warning("pinned_refresh_failed_admin_propose")

    return ProposeResponse(markdown=markdown, candidates=candidates)


@router.post("/pick")
async def admin_pick(
    req: PickRequest,
    x_admin_api_key: str | None = Header(default=None),
) -> PickResponse:
    """Trigger Flow B's pick-parse path without sending to Telegram.

    Creates ``pending_review`` rows for the picked problems. Returns the
    created threads (including ``pending_review_id`` for use in a subsequent
    ``POST /admin/coach`` call). (Google Tasks creation removed 2026-07-31.)
    """
    _check_admin_key(x_admin_api_key)
    log.info("admin_pick_start", picks=req.picks)

    # _pick_parse_path expects a text string like "1 2".
    text = " ".join(str(n) for n in req.picks)
    chat_id = get_settings().telegram_chat_id or "0"

    created = await _pick_parse_path(chat_id, text, dry_run=True)

    picked = [
        PickThreadOut(
            pick_index=t["pick_index"],
            problem_slug=t["problem_slug"],
            problem_title=t["problem_title"],
            difficulty=t["difficulty"],
            message_id=t["message_id"],
            pending_review_id=t["pending_review_id"],
        )
        for t in created
    ]
    log.info("admin_pick_done", picked_count=len(picked))

    # FR-8.2: refresh pinned message after admin-driven pick.
    try:
        from leetcode_coach.flows.pinned import refresh_pinned_message

        await refresh_pinned_message()
    except Exception:
        log.warning("pinned_refresh_failed_admin_pick")

    return PickResponse(picked=picked)


@router.post("/coach")
async def admin_coach(
    req: CoachRequest,
    x_admin_api_key: str | None = Header(default=None),
) -> CoachResponse:
    """Trigger Flow B's coach pass for a submission without sending to Telegram.

    Finds the target ``pending_review`` row (by ``pending_review_id`` or
    ``problem_slug``), calls the coach LLM, runs the lesson decision and
    post-coach updates (DB writes + Google Task update), and returns the full
    coach result + lesson outcome as JSON.
    """
    _check_admin_key(x_admin_api_key)
    log.info(
        "admin_coach_start",
        pending_review_id=req.pending_review_id,
        problem_slug=req.problem_slug,
    )

    # Find the pending_review row.
    today = datetime.date.today()
    with next(get_session()) as session:
        review: PendingReview | None = None
        if req.pending_review_id is not None:
            review = session.get(PendingReview, req.pending_review_id)
        elif req.problem_slug is not None:
            review = session.exec(
                select(PendingReview).where(
                    PendingReview.problem_slug == req.problem_slug,
                    PendingReview.proposed_at == today,
                    PendingReview.status == "open",
                )
            ).first()

    if review is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "No open pending_review row found. Run /admin/propose then "
                "/admin/pick first, or provide a valid pending_review_id."
            ),
        )

    chat_id = get_settings().telegram_chat_id or "0"
    result, lesson_outcome, reply_text = await _coach_pass_path(
        chat_id=chat_id,
        inbound_message_id=-1,
        review=review,
        user_text=req.code,
        dry_run=True,
    )

    log.info(
        "admin_coach_done",
        problem_slug=review.problem_slug,
        status=result.status,
        lesson_action=lesson_outcome.action,
    )

    # FR-8.2: refresh pinned message after admin-driven coach pass.
    try:
        from leetcode_coach.flows.pinned import refresh_pinned_message

        await refresh_pinned_message()
    except Exception:
        log.warning("pinned_refresh_failed_admin_coach")

    return CoachResponse(
        tutor_feedback=result.tutor_feedback,
        lesson_title=result.lesson_title,
        lesson_category=result.lesson_category,
        lesson_is_recurring=result.lesson_is_recurring,
        lesson_should_graduate=result.lesson_should_graduate,
        solved=result.solved,
        status=result.status,
        next_step=result.next_step,
        time_spent_min=result.time_spent_min,
        lesson_action=lesson_outcome.action,
        lesson_title_outcome=lesson_outcome.title,
        times_reinforced=lesson_outcome.times_reinforced,
        reply_text=reply_text,
    )
