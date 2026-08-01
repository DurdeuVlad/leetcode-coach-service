"""Scheduler service topology tests."""
from __future__ import annotations

from leetcode_coach.scheduling import cron


def test_scheduler_registers_phase_9_schedule() -> None:
    """The scheduler-only process owns all five UTC-independent jobs."""
    scheduler = cron.start_scheduler()
    try:
        jobs = {job.id: str(job.trigger) for job in scheduler.get_jobs()}
        assert set(jobs) == {
            "daily_tax",
            "queue_refill",
            "nudge",
            "expiry_sweep",
            "leetcode_refresh_pool",
        }
        assert "hour='22'" in jobs["expiry_sweep"]
        assert "hour='9'" in jobs["queue_refill"]
    finally:
        cron.stop_scheduler()


def test_non_leader_closes_connection(monkeypatch) -> None:
    """A second replica does not retain a connection or register jobs."""
    from leetcode_coach import scheduler as scheduler_module

    class Cursor:
        def execute(self, *_args) -> None:
            pass

        def fetchone(self) -> tuple[bool]:
            return (False,)

        def close(self) -> None:
            pass

    class Connection:
        closed = False

        def cursor(self) -> Cursor:
            return Cursor()

        def close(self) -> None:
            self.closed = True

    connection = Connection()
    monkeypatch.setattr(scheduler_module.engine, "raw_connection", lambda: connection)

    assert scheduler_module.try_acquire_scheduler_lock() is None
    assert connection.closed is True
