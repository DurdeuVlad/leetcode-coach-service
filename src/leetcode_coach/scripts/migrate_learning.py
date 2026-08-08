"""One-shot import of learning data from V1 into the fresh V2 database."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlmodel import Session

from leetcode_coach.config import get_settings
from leetcode_coach.db.base import create_db_engine
from leetcode_coach.domain.migration import import_learning_data


def main() -> None:
    settings = get_settings()
    if not settings.legacy_database_url:
        raise SystemExit("LEGACY_DATABASE_URL is required and must be read-only")
    source = create_engine(settings.legacy_database_url, pool_pre_ping=True)
    target = create_db_engine(settings.database_url)
    with Session(target) as session:
        counts = import_learning_data(source, session, chat_id=int(settings.telegram_chat_id))
        session.commit()
    print(counts)


if __name__ == "__main__":
    main()
