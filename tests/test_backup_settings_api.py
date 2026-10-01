import gzip
import os
import sqlite3
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from btv import settings
from btv.backup import prune_backups
from btv.db import session_scope
from btv.jobs import runner


def test_backup_job_creates_restorable_gzip(cfg):
    j = runner.run_inline("backup", cfg=cfg)
    assert j.status == "succeeded", j.error
    restored = cfg.data_dir / "restored.sqlite"
    restored.write_bytes(gzip.open(j.result["path"]).read())
    tables = {r[0] for r in sqlite3.connect(restored).execute("select name from sqlite_master")}
    assert "source_listings" in tables


def test_backup_of_multi_page_db_completes(cfg):
    """Regression: a stepped backup restarted on every heartbeat write and never finished."""
    from btv.db import session_scope
    from btv.models import JobEvent, Job

    with session_scope(cfg) as s:
        j = Job(kind="filler", status="succeeded")
        s.add(j)
        s.flush()
        s.add_all(JobEvent(job_id=j.id, message="x" * 2000) for _ in range(800))  # ~1.6 MB, many pages
    job = runner.run_inline("backup", cfg=cfg)
    assert job.status == "succeeded", job.error
    assert job.percent == 100.0


def test_prune_keeps_daily_and_weekly(cfg):
    cfg.backup_dir.mkdir(parents=True)
    start = datetime(2026, 1, 1, 3, 0, 0)
    for i in range(60):
        for h in (0, 12):  # two backups per day
            ts = start + timedelta(days=i, hours=h)
            (cfg.backup_dir / f"btv-{ts:%Y%m%d-%H%M%S}.sqlite.gz").write_bytes(b"x")
    prune_backups(cfg)
    kept = sorted(os.listdir(cfg.backup_dir))
    assert kept[-1] == "btv-20260301-150000.sqlite.gz"
    days = {k[4:12] for k in kept}
    assert len(days) == len(kept)  # at most one per day
    assert 14 <= len(kept) <= 14 + 8


def test_settings_validation(cfg):
    with session_scope(cfg) as s:
        assert settings.get(s, "target_move_in") == "2027-06-01"
        settings.set_value(s, "target_move_in", "2027-07-01")
        assert settings.target_date(s).month == 7
        with pytest.raises(ValueError):
            settings.set_value(s, "target_move_in", "June")
        with pytest.raises(KeyError):
            settings.set_value(s, "bogus", 1)


def test_api_endpoints(cfg, file_source):
    from btv.api.app import create_app

    with TestClient(create_app(cfg, start_worker=False)) as c:
        h = c.get("/api/health").json()
        assert h["ok"] is True and h["sources"][0]["state"] == "never_run"
        r = c.put("/api/settings", json={"target_move_in": "2027-05-15", "near_miss_days": 30})
        assert r.json()["near_miss_days"] == 30
        assert c.put("/api/settings", json={"target_move_in": "nope"}).status_code == 400
        job_id = c.post("/api/jobs", json={"kind": "scrape", "source_id": "demo"}).json()["id"]
        runner.run_job(job_id, cfg)
        j = c.get(f"/api/jobs/{job_id}").json()
        assert j["status"] == "succeeded" and j["percent"] == 100.0
        off = c.post("/api/sources/demo/enabled", json={"enabled": False, "reason": "testing"}).json()
        assert off["state"] == "disabled" and off["reasons"] == ["testing"]
        assert c.get("/").status_code == 200
