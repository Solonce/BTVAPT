"""Job runner with persistent progress reporting.

Every long-running piece of work is a ``Job`` row. The code doing the work
receives a ``JobContext`` and reports ``set_total`` / ``advance`` / ``step``;
those updates are written to the DB (throttled) so the CLI, the
``/api/jobs`` endpoint and the status page all show the same live progress.

Only one job executes at a time across all processes (file lock), which keeps
memory predictable (one headless browser at most) and staggers traffic.
"""

from __future__ import annotations

import fcntl
import os
import time
import traceback
from contextlib import contextmanager
from datetime import timedelta
from typing import Callable, Iterator, Protocol

from btv.config import Config, get_config
from btv.db import session_scope, utcnow
from btv.models import Job, JobEvent

STALE_AFTER = timedelta(minutes=15)
ACTIVE = ("queued", "running", "cancel_requested")


class JobCancelled(Exception):
    pass


class JobFn(Protocol):
    def __call__(self, ctx: "JobContext", **params) -> dict | None: ...


_REGISTRY: dict[str, JobFn] = {}


def job(kind: str) -> Callable[[JobFn], JobFn]:
    def deco(fn: JobFn) -> JobFn:
        _REGISTRY[kind] = fn
        return fn

    return deco


def registered_kinds() -> list[str]:
    _load_builtin_jobs()
    return sorted(_REGISTRY)


def _load_builtin_jobs() -> None:
    # Import modules that register jobs via @job.
    import btv.backup  # noqa: F401
    import btv.geocode  # noqa: F401
    import btv.ingest  # noqa: F401
    import btv.pipeline  # noqa: F401


class JobContext:
    """Handle passed to job functions for progress reporting."""

    def __init__(self, job_id: int, cfg: Config, on_update: Callable[["JobContext"], None] | None = None,
                 flush_interval: float = 1.0):
        self.job_id = job_id
        self.cfg = cfg
        self.total: int | None = None
        self.done = 0
        self.step_text: str | None = None
        self.message: str | None = None
        self._on_update = on_update
        self._flush_interval = flush_interval
        self._last_flush = 0.0

    @property
    def percent(self) -> float | None:
        if not self.total:
            return None
        return round(min(100.0, 100.0 * self.done / self.total), 1)

    def set_total(self, total: int | None) -> None:
        self.total = total
        self._flush(force=True)

    def add_total(self, n: int) -> None:
        self.total = (self.total or 0) + n
        self._flush(force=True)

    def advance(self, n: int = 1, step: str | None = None) -> None:
        self.done += n
        if step is not None:
            self.step_text = step
        self._flush()

    def step(self, text: str) -> None:
        self.step_text = text
        self._flush(force=True)

    def log(self, message: str, level: str = "info") -> None:
        with session_scope(self.cfg) as s:
            s.add(JobEvent(job_id=self.job_id, level=level, message=message[:4000]))
        self.message = message
        if self._on_update:
            self._on_update(self)

    def heartbeat(self) -> None:
        self._flush(force=True)

    def _flush(self, force: bool = False) -> None:
        if self._on_update:
            self._on_update(self)
        now = time.monotonic()
        if not force and now - self._last_flush < self._flush_interval:
            return
        self._last_flush = now
        with session_scope(self.cfg) as s:
            j = s.get(Job, self.job_id)
            j.total, j.done, j.step = self.total, self.done, self.step_text
            j.heartbeat_at = utcnow()
            if j.status == "cancel_requested":
                raise JobCancelled()


# ------------------------------------------------------------------ queue ops


def enqueue(kind: str, *, source_id: str | None = None, params: dict | None = None,
            cfg: Config | None = None, dedupe: bool = True) -> int:
    """Queue a job; with ``dedupe`` an identical active job is reused."""
    _load_builtin_jobs()
    if kind not in _REGISTRY:
        raise KeyError(f"unknown job kind {kind!r}; known: {sorted(_REGISTRY)}")
    params = params or {}
    with session_scope(cfg) as s:
        if dedupe:
            existing = (
                s.query(Job)
                .filter(Job.kind == kind, Job.source_id == source_id, Job.status.in_(ACTIVE))
                .order_by(Job.id)
                .all()
            )
            for j in existing:
                if (j.params or {}) == params:
                    return j.id
        j = Job(kind=kind, source_id=source_id, params=params, status="queued")
        s.add(j)
        s.flush()
        return j.id


def request_cancel(job_id: int, cfg: Config | None = None) -> str:
    with session_scope(cfg) as s:
        j = s.get(Job, job_id)
        if j is None:
            raise KeyError(job_id)
        if j.status == "queued":
            j.status, j.finished_at = "cancelled", utcnow()
        elif j.status == "running":
            j.status = "cancel_requested"
        return j.status


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def recover_stale(cfg: Config | None = None) -> list[int]:
    """Mark running jobs whose process died or stopped heart-beating as interrupted."""
    recovered = []
    cutoff = utcnow() - STALE_AFTER
    with session_scope(cfg) as s:
        for j in s.query(Job).filter(Job.status.in_(("running", "cancel_requested"))).all():
            dead = j.pid != os.getpid() and not _pid_alive(j.pid)
            silent = (j.heartbeat_at or j.started_at or j.created_at) < cutoff
            if dead or silent:
                j.status = "interrupted"
                j.finished_at = utcnow()
                j.error = "process exited" if dead else "no heartbeat for 15 minutes"
                recovered.append(j.id)
    return recovered


@contextmanager
def heavy_lock(cfg: Config, blocking: bool = True) -> Iterator[bool]:
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    fh = open(cfg.lock_path, "a+")
    try:
        flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        try:
            fcntl.flock(fh, flags)
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        fh.close()


def run_job(job_id: int, cfg: Config | None = None,
            on_update: Callable[[JobContext], None] | None = None) -> Job:
    """Execute a queued job in this process, holding the global heavy lock."""
    cfg = cfg or get_config()
    _load_builtin_jobs()
    with heavy_lock(cfg):
        with session_scope(cfg) as s:
            j = s.get(Job, job_id)
            if j.status != "queued":
                return j
            j.status, j.started_at, j.heartbeat_at, j.pid = "running", utcnow(), utcnow(), os.getpid()
            kind, params, source_id = j.kind, dict(j.params or {}), j.source_id
        ctx = JobContext(job_id, cfg, on_update=on_update)
        if source_id is not None:
            params.setdefault("source_id", source_id)
        status, error, result = "succeeded", None, None
        try:
            result = _REGISTRY[kind](ctx, **params)
        except JobCancelled:
            status = "cancelled"
        except Exception as exc:  # noqa: BLE001 - job isolation boundary
            status, error = "failed", f"{type(exc).__name__}: {exc}"
            ctx.log(traceback.format_exc(), level="error")
        with session_scope(cfg) as s:
            j = s.get(Job, job_id)
            j.status, j.error, j.result = status, error, result
            j.total, j.done, j.step = ctx.total, ctx.done, ctx.step_text
            if status == "succeeded":
                j.step = "done"
                if j.total:
                    j.done = j.total
            j.finished_at = j.heartbeat_at = utcnow()
        with session_scope(cfg) as s:
            j = s.get(Job, job_id)
            s.expunge(j)
            return j


def run_inline(kind: str, *, source_id: str | None = None, params: dict | None = None,
               cfg: Config | None = None, on_update: Callable[[JobContext], None] | None = None) -> Job:
    job_id = enqueue(kind, source_id=source_id, params=params, cfg=cfg, dedupe=False)
    return run_job(job_id, cfg, on_update=on_update)


def next_queued(cfg: Config | None = None) -> int | None:
    with session_scope(cfg) as s:
        j = s.query(Job).filter(Job.status == "queued").order_by(Job.id).first()
        return j.id if j else None


def job_to_dict(j: Job, events: int = 0) -> dict:
    d = {
        "id": j.id,
        "kind": j.kind,
        "source_id": j.source_id,
        "params": j.params,
        "status": j.status,
        "percent": j.percent,
        "done": j.done,
        "total": j.total,
        "step": j.step,
        "error": j.error,
        "result": j.result,
        "created_at": _iso(j.created_at),
        "started_at": _iso(j.started_at),
        "finished_at": _iso(j.finished_at),
        "heartbeat_at": _iso(j.heartbeat_at),
    }
    if j.started_at:
        end = j.finished_at or utcnow()
        d["elapsed_seconds"] = round((end - j.started_at).total_seconds(), 1)
        if j.status == "running" and j.total and j.done:
            rate = d["elapsed_seconds"] / j.done
            d["eta_seconds"] = round(rate * max(0, j.total - j.done), 0)
    if events:
        d["events"] = [
            {"at": _iso(e.at), "level": e.level, "message": e.message} for e in j.events[-events:]
        ]
    return d


def _iso(dt) -> str | None:
    return dt.isoformat() + "Z" if dt else None
