from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def test_v2_0002_upgrade_downgrade_upgrade_roundtrip(tmp_path, monkeypatch):
    database = tmp_path / "roundtrip.db"
    url = f"sqlite:///{database.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")

    command.upgrade(config, "head")
    command.downgrade(config, "v2_0001")
    command.upgrade(config, "head")

    engine = create_engine(url)
    assert "attempt_id" in {
        column["name"] for column in inspect(engine).get_columns("v2_credit_ledger")
    }
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "v2_0002"
        )


def test_v2_0002_downgrade_tolerates_old_problem_table_without_new_index(tmp_path, monkeypatch):
    database = tmp_path / "old-schema-roundtrip.db"
    url = f"sqlite:///{database.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")

    command.upgrade(config, "head")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("DROP INDEX ix_v2_problems_verified_solved"))
    assert "ix_v2_problems_verified_solved" not in {
        index["name"] for index in inspect(engine).get_indexes("v2_problems")
    }

    command.downgrade(config, "v2_0001")
    command.upgrade(config, "head")

    assert "verified_solved" in {
        column["name"] for column in inspect(engine).get_columns("v2_problems")
    }


def test_v2_0002_upgrade_creates_verified_solved_index_on_a_genuinely_old_schema(
    tmp_path, monkeypatch
):
    """Reproduce the real production upgrade path: a v2_0001 database created before
    this migration existed, so `verified_solved` (and its index) is fully absent —
    unlike a fresh DB, where v2_0001's `metadata.create_all()` against current models
    creates both column and index incidentally."""
    database = tmp_path / "genuinely-old.db"
    url = f"sqlite:///{database.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")

    command.upgrade(config, "v2_0001")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("DROP INDEX ix_v2_problems_verified_solved"))
        connection.execute(text("ALTER TABLE v2_problems DROP COLUMN verified_solved"))
    assert "verified_solved" not in {
        column["name"] for column in inspect(engine).get_columns("v2_problems")
    }

    command.upgrade(config, "head")

    assert "verified_solved" in {
        column["name"] for column in inspect(engine).get_columns("v2_problems")
    }
    assert "ix_v2_problems_verified_solved" in {
        index["name"] for index in inspect(engine).get_indexes("v2_problems")
    }
