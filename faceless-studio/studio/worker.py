"""Background loop: runs queued jobs one at a time and drives autopilot."""
from __future__ import annotations

import logging
import random
import threading
import time

from . import db, formats, pipeline, settings, topics

log = logging.getLogger(__name__)

_state = {"last_autopilot_check": 0.0, "autopilot_note": "", "busy": False}
_wake = threading.Event()


def state() -> dict:
    return dict(_state)


def wake() -> None:
    _wake.set()


def enqueue(title: str, fmt: str, preset: str = "autopilot", overrides: dict | None = None,
            topic_id: int | None = None, source_video: str | None = None, origin: str = "manual",
            script: dict | None = None) -> int:
    resolved = settings.resolve(preset, overrides)
    job_id = db.create_job(title, fmt, preset, resolved.model_dump(), topic_id=topic_id,
                           source_video=source_video, origin=origin, script=script)
    wake()
    return job_id


def _autopilot_tick() -> None:
    s = settings.load()
    if not s.autopilot or s.videos_per_day <= 0:
        _state["autopilot_note"] = "off"
        return
    day_start = time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d"))
    today = db.row("SELECT COUNT(*) AS n FROM jobs WHERE origin='autopilot' AND created_at >= ?", day_start)["n"]
    pending = db.row("SELECT COUNT(*) AS n FROM jobs WHERE origin='autopilot' AND status IN "
                     "('queued','running','awaiting_approval')")["n"]
    last = db.row("SELECT MAX(created_at) AS t FROM jobs WHERE origin='autopilot'")["t"] or 0
    gap = 86400 / s.videos_per_day
    if today >= s.videos_per_day:
        _state["autopilot_note"] = f"{today}/{s.videos_per_day} made today"
        return
    if pending:
        _state["autopilot_note"] = "waiting for current video"
        return
    if time.time() - last < gap:
        _state["autopilot_note"] = f"next video in {int((gap - (time.time() - last)) / 60)} min"
        return
    if db.month_spend() >= s.monthly_budget_usd:
        _state["autopilot_note"] = "monthly budget reached"
        return
    added = topics.ensure_pool(s)
    if added:
        log.info("autopilot refilled topic pool (+%d)", added)
    weights = {k: v for k, v in s.format_weights.items() if v > 0 and k in formats.all_formats()}
    fmt = random.choices(list(weights), list(weights.values()))[0] if weights else None
    t = db.take_topic(fmt) or db.take_topic()
    if not t:
        _state["autopilot_note"] = "topic pool empty"
        return
    enqueue(t["title"], t["format"], "autopilot", topic_id=t["id"], origin="autopilot")
    _state["autopilot_note"] = f"queued: {t['title']}"


def loop() -> None:
    with db.tx() as c:
        c.execute("UPDATE jobs SET status='queued' WHERE status='running'")  # crash recovery
    while True:
        try:
            if time.time() - _state["last_autopilot_check"] > 60:
                _state["last_autopilot_check"] = time.time()
                _autopilot_tick()
            job = db.row("SELECT id FROM jobs WHERE status='queued' ORDER BY id LIMIT 1")
            if job:
                _state["busy"] = True
                pipeline.run(job["id"])
                _state["busy"] = False
                continue
        except Exception:
            log.exception("worker loop error")
            _state["busy"] = False
        _wake.wait(5)
        _wake.clear()


def start() -> threading.Thread:
    t = threading.Thread(target=loop, name="studio-worker", daemon=True)
    t.start()
    return t
