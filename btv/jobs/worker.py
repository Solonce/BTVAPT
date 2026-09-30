"""Background worker: schedules due work and drains the job queue serially."""

from __future__ import annotations

import logging
import threading
from datetime import timedelta

from btv.config import Config, get_config
from btv.db import session_scope, utcnow
from btv.jobs.runner import enqueue, next_queued, recover_stale, run_job
from btv.models import Job

log = logging.getLogger("btv.worker")

BACKUP_EVERY = timedelta(hours=24)


class Worker:
    def __init__(self, cfg: Config | None = None, poll_seconds: float = 15.0, schedule_seconds: float = 60.0):
        self.cfg = cfg or get_config()
        self.poll_seconds = poll_seconds
        self.schedule_seconds = schedule_seconds
        self._stop = threading.Event()
        self._last_schedule = None

    def stop(self) -> None:
        self._stop.set()

    def tick(self) -> int | None:
        """One iteration: schedule if due, then run at most one queued job."""
        now = utcnow()
        if self._last_schedule is None or (now - self._last_schedule).total_seconds() >= self.schedule_seconds:
            self._last_schedule = now
            recover_stale(self.cfg)
            self._schedule()
        job_id = next_queued(self.cfg)
        if job_id is not None:
            j = run_job(job_id, self.cfg)
            log.info("job %s %s -> %s", j.id, j.kind, j.status)
        return job_id

    def _schedule(self) -> None:
        # scrape_due is cheap and just enqueues per-source scrapes.
        enqueue("scrape_due", cfg=self.cfg)
        with session_scope(self.cfg) as s:
            last = (
                s.query(Job)
                .filter(Job.kind == "backup", Job.status.in_(("succeeded", "queued", "running")))
                .order_by(Job.id.desc())
                .first()
            )
            due = last is None or (last.status == "succeeded" and utcnow() - (last.finished_at or last.created_at) >= BACKUP_EVERY)
        if due:
            enqueue("backup", cfg=self.cfg)

    def run_forever(self) -> None:
        log.info("worker started")
        while not self._stop.is_set():
            try:
                ran = self.tick()
            except Exception:  # noqa: BLE001 - never let the loop die
                log.exception("worker tick failed")
                ran = None
            if ran is None:
                self._stop.wait(self.poll_seconds)

    def start_thread(self) -> threading.Thread:
        t = threading.Thread(target=self.run_forever, name="btv-worker", daemon=True)
        t.start()
        return t
