"""Auditable credit ledger operations for the Phase 9 budget loop."""

from __future__ import annotations

import datetime
from decimal import Decimal

from sqlmodel import Session, func, select

from leetcode_coach.db.base import get_session
from leetcode_coach.db.models import CreditLedger, CreditReason, LeetCodeLog

_SOLVE_CREDITS = {"easy": Decimal("0.5"), "medium": Decimal("1"), "hard": Decimal("2")}


def credits_for(status: str, difficulty: str) -> Decimal:
    """Return the configured provisional reward for one recorded outcome."""
    if status == "solved":
        return _SOLVE_CREDITS.get(difficulty.lower(), Decimal("0"))
    if status == "reviewed":
        return Decimal("0.5")
    if status == "saw_solution":
        return Decimal("0.25")
    return Decimal("0")


def balance(session: Session) -> Decimal:
    """Compute balance from the append-only ledger; no cached balance exists."""
    return Decimal(session.exec(select(func.coalesce(func.sum(CreditLedger.amount), 0))).one())


def accrue_daily_tax(session: Session, *, on_date: datetime.date | None = None) -> bool:
    """Insert at most one tax ledger record for a calendar day."""
    date = on_date or datetime.date.today()
    key = f"tax:{date.isoformat()}"
    if session.exec(select(CreditLedger.id).where(CreditLedger.idempotency_key == key)).first():
        return False
    session.add(CreditLedger(idempotency_key=key, amount=Decimal("-2"), reason=CreditReason.DAILY_TAX))
    return True


def award_review(
    session: Session,
    *,
    review_id: int | None,
    log: LeetCodeLog,
    difficulty: str,
) -> Decimal:
    """Add the one idempotent reward corresponding to a newly-created log row."""
    if log.id is None:
        raise ValueError("credit award requires a persisted log row")
    amount = credits_for(log.status, difficulty)
    key = f"log:{log.id}"
    if session.exec(select(CreditLedger.id).where(CreditLedger.idempotency_key == key)).first():
        return amount
    reason = CreditReason(log.status)
    log.credits_earned = amount
    session.add(log)
    session.add(
        CreditLedger(
            idempotency_key=key,
            amount=amount,
            reason=reason,
            review_id=review_id,
            log_id=log.id,
        )
    )
    return amount


def format_balance(value: Decimal) -> str:
    """Format balance in user-facing daily-tax units."""
    if value == 0:
        return "0 (on pace)"
    direction = "ahead" if value > 0 else "behind"
    return f"{value:+.1f} ({direction} {abs(value) / Decimal('2'):g} days)"


async def apply_daily_tax() -> None:
    """Scheduled entrypoint; idempotency lives in the ledger key."""
    with next(get_session()) as session:
        changed = accrue_daily_tax(session)
        if changed:
            session.commit()
