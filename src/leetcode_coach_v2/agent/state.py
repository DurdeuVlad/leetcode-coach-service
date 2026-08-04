"""Durable approval state independent of the database implementation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

ApprovalDecision = Literal["approve", "reject"]


@dataclass(frozen=True, slots=True)
class PendingApproval:
    """A Telegram-safe projection of one SDK tool interruption."""

    approval_id: str
    tool_name: str
    call_id: str | None
    arguments: dict[str, Any]
    summary: str


@dataclass(slots=True)
class SerializedRunState:
    """The database payload for a paused SDK run.

    ``sdk_state`` is the exact output of ``RunState.to_json``.  It is intentionally
    opaque to the domain layer; this avoids pretending we can reconstruct an SDK
    continuation from a hand-written event format.
    """

    chat_id: int
    run_id: str
    sdk_state: dict[str, Any]
    approvals: list[PendingApproval]
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime = field(default_factory=lambda: datetime.now(UTC) + timedelta(hours=24))

    @classmethod
    def new(
        cls,
        *,
        chat_id: int,
        sdk_state: dict[str, Any],
        approvals: list[PendingApproval],
        ttl_hours: int = 24,
    ) -> SerializedRunState:
        now = datetime.now(UTC)
        return cls(
            chat_id=chat_id,
            run_id=str(uuid4()),
            sdk_state=sdk_state,
            approvals=approvals,
            created_at=now,
            expires_at=now + timedelta(hours=ttl_hours),
        )

    @property
    def expired(self) -> bool:
        return self.expires_at <= datetime.now(UTC)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["created_at"] = self.created_at.isoformat()
        data["expires_at"] = self.expires_at.isoformat()
        return data


def text_confirmation(
    text: str,
    *,
    replying_to_approval: bool,
    pending_approval_count: int,
) -> ApprovalDecision | None:
    """Accept only exact yes/no at the Telegram boundary.

    A generic conversational "yes" is not a permission slip when multiple actions
    are awaiting a decision.
    """

    normalized = text.strip().casefold()
    if normalized not in {"yes", "no"}:
        return None
    if not replying_to_approval and pending_approval_count != 1:
        return None
    return "approve" if normalized == "yes" else "reject"
