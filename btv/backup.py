"""Database backups with progress reporting and daily/weekly retention.

Uses SQLite's online backup API (safe while the service is writing), then
gzips the copy into ``data/backups/btv-YYYYmmdd-HHMMSS.sqlite.gz``.

The raw archive is append-only and content-addressed, so it needs no
snapshotting: copy ``data/archive`` with rsync if you want it off-box.
"""

from __future__ import annotations

import gzip
import os
import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from btv.config import Config
from btv.db import utcnow
from btv.jobs.runner import JobContext, job

_NAME = re.compile(r"^btv-(\d{8})-(\d{6})\.sqlite\.gz$")


def backup_db(cfg: Config, ctx: JobContext | None = None) -> Path:
    cfg.backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = utcnow().strftime("%Y%m%d-%H%M%S")
    raw = cfg.backup_dir / f"btv-{stamp}.sqlite"
    final = raw.with_suffix(".sqlite.gz")

    src = sqlite3.connect(cfg.db_path)
    dst = sqlite3.connect(raw)
    if ctx:
        ctx.step("copying database")

    def progress(_status, remaining, total):
        if ctx and total:
            ctx.total = total * 2  # copy + compress phases
            ctx.done = total - remaining
            ctx.heartbeat()

    try:
        src.backup(dst, pages=256, progress=progress)
    finally:
        dst.close()
        src.close()

    if ctx:
        ctx.step("compressing")
    size = raw.stat().st_size
    chunk = 1 << 20
    with open(raw, "rb") as fin, gzip.open(final.with_suffix(".tmp"), "wb") as fout:
        read = 0
        while block := fin.read(chunk):
            fout.write(block)
            read += len(block)
            if ctx and ctx.total and size:
                ctx.done = ctx.total // 2 + int((ctx.total // 2) * read / size)
                ctx.heartbeat()
    os.replace(final.with_suffix(".tmp"), final)
    raw.unlink()
    return final


def _parse_stamp(p: Path) -> datetime | None:
    m = _NAME.match(p.name)
    return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S") if m else None


def prune_backups(cfg: Config) -> list[Path]:
    """Keep the newest backup of each of the last N days and last M ISO weeks."""
    backups = sorted(
        ((ts, p) for p in cfg.backup_dir.glob("btv-*.sqlite.gz") if (ts := _parse_stamp(p))),
        reverse=True,
    )
    keep: set[Path] = set()
    days: list = []
    weeks: list = []
    for ts, p in backups:
        d = ts.date()
        if d not in days and len(days) < cfg.keep_daily:
            days.append(d)
            keep.add(p)
        w = ts.isocalendar()[:2]
        if w not in weeks and len(weeks) < cfg.keep_weekly:
            weeks.append(w)
            keep.add(p)
    removed = []
    for _, p in backups:
        if p not in keep:
            p.unlink()
            removed.append(p)
    return removed


@job("backup")
def backup_job(ctx: JobContext, **_ignored) -> dict:
    path = backup_db(ctx.cfg, ctx)
    ctx.step("pruning old backups")
    removed = prune_backups(ctx.cfg)
    usage = shutil.disk_usage(ctx.cfg.backup_dir)
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "pruned": [p.name for p in removed],
        "disk_free_gb": round(usage.free / 1e9, 1),
    }
