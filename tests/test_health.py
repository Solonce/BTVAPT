from datetime import timedelta

from btv.db import session_scope, utcnow
from btv.health import next_due, source_health
from btv.jobs import runner
from btv.models import Job, Source


def make(**kw):
    base = dict(id="s", name="S", platform="file", interval_minutes=60, enabled=True, consecutive_failures=0)
    base.update(kw)
    return Source(**base)


def test_states(cfg):
    now = utcnow()
    assert source_health(make(enabled=False), cfg)["state"] == "disabled"
    assert source_health(make(), cfg)["state"] == "never_run"
    ok = make(last_run_at=now, last_success_at=now, last_status="ok")
    assert source_health(ok, cfg)["state"] == "ok"
    stale = make(last_run_at=now, last_success_at=now - timedelta(hours=5), last_status="ok")
    assert source_health(stale, cfg)["state"] == "stale"
    failing = make(last_run_at=now, last_success_at=now, last_status="failed", last_error="HTTP 500")
    h = source_health(failing, cfg)
    assert h["state"] == "failing" and h["reasons"] == ["HTTP 500"]
    assert source_health(make(last_run_at=now, last_status="unhealthy"), cfg)["state"] == "degraded"


def test_next_due_backs_off_after_failures():
    now = utcnow()
    assert next_due(make(last_run_at=now)) == now + timedelta(minutes=60)
    assert next_due(make(last_run_at=now, consecutive_failures=2)) == now + timedelta(minutes=240)
    assert next_due(make(last_run_at=now, consecutive_failures=10)) == now + timedelta(minutes=960)
    assert next_due(make(enabled=False)) is None


def test_scrape_due_enqueues_only_due_sources(cfg):
    now = utcnow()
    with session_scope(cfg) as s:
        s.add(make(id="due", last_run_at=now - timedelta(hours=2)))
        s.add(make(id="fresh", last_run_at=now))
        s.add(make(id="off", enabled=False))
    j = runner.run_inline("scrape_due", cfg=cfg)
    assert j.result == {"queued": ["due"]}
    with session_scope(cfg) as s:
        assert [x.source_id for x in s.query(Job).filter_by(kind="scrape")] == ["due"]
