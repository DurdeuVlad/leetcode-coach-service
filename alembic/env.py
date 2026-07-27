"""Alembic env — uses the app's SQLModel metadata + DATABASE_URL from settings.

This wires migrations to the same engine the app uses, so `alembic upgrade
head` (run by the container entrypoint) creates exactly the tables the app
expects. Models are imported for their side effect on `SQLModel.metadata`;
when #003 lands, the four tables appear here automatically.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Import settings + SQLModel metadata + models (side effect: registers tables).
from leetcode_coach.config import get_settings
from leetcode_coach.db.base import SQLModel
from leetcode_coach.db import models  # noqa: F401  (registers tables on metadata)

config = context.config

# Inject the runtime DATABASE_URL (overrides alembic.ini placeholder).
config.set_main_option("sqlalchemy.url", get_settings().database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
