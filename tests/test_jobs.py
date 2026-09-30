import os

import pytest

from btv.db import session_scope, utcnow
from btv.jobs import runner
from btv.models import Job, JobEvent


@pytest.fixture
def kinds():
    @runner.job("_test_progress")
    def _progress(ctx, n=5, **_):
        ctx.set_total(n)
        for i in range(n):
            ctx.advance(step=f"item {i}")
        return {"n": n}

    @runner.job("_test_fail")
    def _fail(ctx, **_):
        ctx.set_total(10)
        ctx.advance(3)
        raise RuntimeError("boom")


def test_progress_is_persisted(cfg, kinds):
    updates = []
    j = runner.run_inline("_test_progress", params={"n": 4}, cfg=cfg,
                          on_update=lambda c: updates.append((c.done, c.total)))
    assert j.status == "succeeded"
    assert j.result == {"n": 4}
    assert (j.done, j.total, j.percent, j.step) == (4, 4, 100.0, "done")
    assert updates[-1] == (4, 4)


def test_failure_is_recorded_with_partial_progress(cfg, kinds):
    j = runner.run_inline("_test_fail", cfg=cfg)
    assert j.status == "failed"
    assert "boom" in j.error
    assert (j.done, j.total) == (3, 10)
    with session_scope(cfg) as s:
        assert s.query(JobEvent).filter_by(job_id=j.id, level="error").count() == 1


def test_enqueue_dedupes_active_jobs(cfg, kinds):
    a = runner.enqueue("_test_progress", params={"n": 1}, cfg=cfg)
    b = runner.enqueue("_test_progress", params={"n": 1}, cfg=cfg)
    c = runner.enqueue("_test_progress", params={"n": 2}, cfg=cfg)
    assert a == b != c


def test_unknown_kind_rejected(cfg):
    with pytest.raises(KeyError):
        runner.enqueue("nope", cfg=cfg)


def test_cancel_queued_and_running(cfg, kinds):
    q = runner.enqueue("_test_progress", cfg=cfg)
    assert runner.request_cancel(q, cfg) == "cancelled"

    @runner.job("_test_cancel_me")
    def _cancel_me(ctx, **_):
        ctx.set_total(3)
        runner.request_cancel(ctx.job_id, cfg)
        ctx.heartbeat()  # forces a flush, which notices the request
        raise AssertionError("should have been cancelled")

    j = runner.run_inline("_test_cancel_me", cfg=cfg)
    assert j.status == "cancelled"


def test_recover_stale_marks_dead_process_interrupted(cfg):
    with session_scope(cfg) as s:
        s.add(Job(kind="scrape", status="running", pid=2**22 + 12345, started_at=utcnow(), heartbeat_at=utcnow()))
        s.add(Job(kind="scrape", status="running", pid=os.getpid(), started_at=utcnow(), heartbeat_at=utcnow()))
    recovered = runner.recover_stale(cfg)
    assert len(recovered) == 1
    with session_scope(cfg) as s:
        statuses = sorted(j.status for j in s.query(Job))
    assert statuses == ["interrupted", "running"]
