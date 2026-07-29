"""Thin query helpers for the ``bot_state`` table (issue #036).

Single responsibility: read/write key-value state. No business logic, no
flow decisions, no HTTP. The first and currently only consumer is the
pinned progression message (#039, key ``pinned_message_id``).

``set_state`` upserts (insert-or-update) and bumps ``updated_at`` to the
current UTC wall-clock time on every write. The upsert is implemented as
select-then-insert-or-update rather than a PG-specific ``ON CONFLICT`` so
it stays portable to the SQLite test engine.

These helpers are intentionally synchronous — they wrap short DB
transactions and are called from async flow code via plain calls (the
session commits before returning, so there's no awaitable I/O leak).
"""

from __future__ import annotations

import datetime

from sqlmodel import select

from leetcode_coach.db.base import get_session
from leetcode_coach.db.models import BotState


def get_state(key: str) -> str | None:
    """Return the stored value for ``key``, or ``None`` if the key is absent.

    The caller parses the JSON-encoded value per key (issue #036
    "explicit over implicit": the column type stays stable across keys).
    """
    with next(get_session()) as session:
        row = session.exec(select(BotState).where(BotState.key == key)).first()
        return row.value if row is not None else None


def set_state(key: str, value: str) -> None:
    """Upsert ``key`` → ``value`` and bump ``updated_at`` to now (UTC).

    On a new key: insert. On an existing key: overwrite ``value`` and
    refresh ``updated_at``. Commits in one transaction.
    """
    now = datetime.datetime.now(datetime.UTC)
    with next(get_session()) as session:
        row = session.exec(select(BotState).where(BotState.key == key)).first()
        if row is None:
            session.add(BotState(key=key, value=value, updated_at=now))
        else:
            row.value = value
            row.updated_at = now
            session.add(row)
        session.commit()
