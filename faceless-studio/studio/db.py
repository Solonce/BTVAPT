"""SQLite store: topic pool, jobs, spend ledger, footage library."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

from . import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS topics (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    format TEXT NOT NULL,
    angle TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'generated',
    status TEXT NOT NULL DEFAULT 'new',      -- new | used | rejected
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    topic_id INTEGER,
    title TEXT NOT NULL,
    format TEXT NOT NULL,
    preset TEXT NOT NULL DEFAULT 'autopilot',
    origin TEXT NOT NULL DEFAULT 'manual',   -- manual | autopilot
    status TEXT NOT NULL DEFAULT 'queued',   -- queued | running | awaiting_approval | done | failed | cancelled
    step TEXT NOT NULL DEFAULT '',
    progress REAL NOT NULL DEFAULT 0,
    error TEXT,
    settings_json TEXT NOT NULL,
    script_json TEXT,
    output_json TEXT,
    log TEXT NOT NULL DEFAULT '',
    source_video TEXT,
    cost_usd REAL NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS spend (
    id INTEGER PRIMARY KEY,
    job_id INTEGER,
    provider TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    usd REAL NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS library (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,                      -- video | image | music
    tags TEXT NOT NULL DEFAULT '',
    duration REAL,
    created_at REAL NOT NULL
);
"""

_local = threading.local()


def conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is None:
        settings.DATA.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(settings.DATA / "studio.db", timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        _local.conn = c
    return c


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    c = conn()
    with c:
        yield c


def rows(sql: str, *args: Any) -> list[dict]:
    return [dict(r) for r in conn().execute(sql, args).fetchall()]


def row(sql: str, *args: Any) -> dict | None:
    r = conn().execute(sql, args).fetchone()
    return dict(r) if r else None


# Topics -------------------------------------------------------------------

def add_topics(items: list[dict], source: str) -> int:
    existing = {r["title"].lower() for r in rows("SELECT title FROM topics")}
    n = 0
    with tx() as c:
        for t in items:
            if t["title"].lower() in existing:
                continue
            c.execute(
                "INSERT INTO topics (title, format, angle, source, created_at) VALUES (?,?,?,?,?)",
                (t["title"], t["format"], t.get("angle", ""), source, time.time()),
            )
            existing.add(t["title"].lower())
            n += 1
    return n


def take_topic(fmt: str | None = None) -> dict | None:
    sql = "SELECT * FROM topics WHERE status='new'" + (" AND format=?" if fmt else "") + " ORDER BY id LIMIT 1"
    t = row(sql, *([fmt] if fmt else []))
    if t:
        with tx() as c:
            c.execute("UPDATE topics SET status='used' WHERE id=?", (t["id"],))
    return t


# Jobs ---------------------------------------------------------------------

def create_job(title: str, fmt: str, preset: str, resolved: dict, topic_id: int | None = None,
               source_video: str | None = None, origin: str = "manual", script: dict | None = None) -> int:
    now = time.time()
    with tx() as c:
        cur = c.execute(
            "INSERT INTO jobs (topic_id, title, format, preset, origin, settings_json, script_json, source_video,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (topic_id, title, fmt, preset, origin, json.dumps(resolved),
             json.dumps(script) if script else None, source_video, now, now),
        )
        return cur.lastrowid


def update_job(job_id: int, **fields: Any) -> None:
    if not fields:
        return
    for k in ("script_json", "output_json"):
        if k in fields and not isinstance(fields[k], (str, type(None))):
            fields[k] = json.dumps(fields[k])
    fields["updated_at"] = time.time()
    cols = ", ".join(f"{k}=?" for k in fields)
    with tx() as c:
        c.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))


def log_job(job_id: int, line: str) -> None:
    stamp = time.strftime("%H:%M:%S")
    with tx() as c:
        c.execute("UPDATE jobs SET log = log || ?, updated_at=? WHERE id=?",
                  (f"[{stamp}] {line}\n", time.time(), job_id))


def job(job_id: int) -> dict | None:
    return row("SELECT * FROM jobs WHERE id=?", job_id)


# Spend --------------------------------------------------------------------

def record_spend(job_id: int | None, provider: str, usd: float, detail: str = "") -> None:
    if usd <= 0:
        return
    with tx() as c:
        c.execute("INSERT INTO spend (job_id, provider, detail, usd, created_at) VALUES (?,?,?,?,?)",
                  (job_id, provider, detail, usd, time.time()))
        if job_id:
            c.execute("UPDATE jobs SET cost_usd = cost_usd + ? WHERE id=?", (usd, job_id))


def month_spend() -> float:
    start = time.mktime(time.strptime(time.strftime("%Y-%m-01"), "%Y-%m-%d"))
    r = row("SELECT COALESCE(SUM(usd),0) AS s FROM spend WHERE created_at >= ?", start)
    return float(r["s"]) if r else 0.0
