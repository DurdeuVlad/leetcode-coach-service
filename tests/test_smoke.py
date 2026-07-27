"""Phase 0 smoke test — proves the skeleton boots and `/health` works.

This is the minimal version of issue #007's smoke test. The full version
(testcontainers Postgres, table-existence checks, row round-trip) lands
when #007 is implemented properly. For now this verifies:

- the app imports without error (all required env vars present);
- `/health` returns 200 against a real Postgres (CI service container).

Requires: ``DATABASE_URL`` pointing at a live Postgres. In CI this is the
service container; locally, run ``docker compose up -d db`` first.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from leetcode_coach.main import app


def test_health_ok() -> None:
    """`/health` returns 200 + status=ok when the DB is reachable."""
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db"] == "reachable"
