"""Phase 0 smoke test — proves the skeleton boots and `/health` works.

This is the minimal version of issue #007's smoke test. The full version
(testcontainers Postgres, table-existence checks, row round-trip) lands
when #007 is implemented properly. For now this verifies:

- the app imports without error (all required env vars present);
- `/health` returns 200 against an in-memory SQLite engine patched in for
  the test (no live Postgres required locally or in CI unit runs).

The production app still uses Postgres via ``DATABASE_URL``; this test
only swaps the engine so the health check has something to ping.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from leetcode_coach import main as main_module
from leetcode_coach.db import base as db_base


@pytest.fixture
def sqlite_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """Swap the module-level engine for in-memory SQLite + create tables.

    Uses StaticPool + check_same_thread=False so the single in-memory
    connection survives across the TestClient's portal thread.
    """
    test_engine = create_engine(
        "sqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(test_engine)

    monkeypatch.setattr(db_base, "engine", test_engine)
    monkeypatch.setattr(main_module, "engine", test_engine)

    def _get_session() -> Iterator[Session]:
        with Session(test_engine) as session:
            yield session

    monkeypatch.setattr(db_base, "get_session", _get_session)
    return main_module.app


def test_health_ok(sqlite_app: FastAPI) -> None:
    """`/health` returns 200 + status=ok when the DB is reachable.

    ``/health`` is the lightweight liveness probe (DB + scheduler only, no
    external HTTP calls). The full external-service probes live in
    ``/health/deep`` and are covered by ``test_connectivity.py``.
    """
    with TestClient(sqlite_app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db"] == "reachable"
    assert "services" not in body  # lightweight — no external probes


def test_health_deep_ok(sqlite_app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    """`/health/deep` returns 200 + a `services` block with probe results.

    The probes are stubbed so the test never makes real HTTP calls — the
    probe logic itself is covered by ``test_connectivity.py``.
    """
    from leetcode_coach.integrations import connectivity

    async def _stub_ping_all() -> list:
        return []

    monkeypatch.setattr(connectivity, "ping_all", _stub_ping_all)
    with TestClient(sqlite_app) as client:
        resp = client.get("/health/deep")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db"] == "reachable"
    assert "services" in body
