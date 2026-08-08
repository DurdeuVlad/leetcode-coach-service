from sqlmodel import Session, create_engine, select

from leetcode_coach.db.models import BaseSQLModel, V2CreditLedger
from leetcode_coach.domain.migration import import_learning_data


def test_imports_only_learning_records_and_not_historical_credits():
    source = create_engine("sqlite://")
    target_engine = create_engine("sqlite://")
    BaseSQLModel.metadata.create_all(target_engine)
    with source.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE leetcode_problems (slug TEXT PRIMARY KEY, title TEXT, url TEXT, difficulty TEXT, tags TEXT, solved BOOLEAN, last_attempted DATE, times_attempted INTEGER)"
        )
        conn.exec_driver_sql(
            "CREATE TABLE leetcode_log (id INTEGER PRIMARY KEY, problem_slug TEXT, date DATE, status TEXT, time_spent_min INTEGER, tutor_feedback TEXT, credits_earned NUMERIC)"
        )
        conn.exec_driver_sql(
            "CREATE TABLE tutor_lessons (id INTEGER PRIMARY KEY, title TEXT, category TEXT, created_at DATE, times_reinforced INTEGER, active BOOLEAN)"
        )
        conn.exec_driver_sql("CREATE TABLE credit_ledger (id INTEGER PRIMARY KEY, amount NUMERIC)")
        conn.exec_driver_sql(
            "INSERT INTO leetcode_problems VALUES ('house-robber', 'House Robber', 'https://lc/198', 'easy', 'dp', 0, NULL, 0)"
        )
        conn.exec_driver_sql(
            "INSERT INTO leetcode_log VALUES (1, 'house-robber', '2026-01-01', 'solved', 10, 'nice', 12)"
        )
        conn.exec_driver_sql(
            "INSERT INTO tutor_lessons VALUES (1, 'DP states', 'dp', '2026-01-01', 2, 1)"
        )
    with Session(target_engine) as target:
        result = import_learning_data(source, target, chat_id=7)
        target.commit()
        assert result == {"problems": 1, "attempts": 1, "lessons": 1}
        assert target.exec(select(V2CreditLedger)).all() == []
