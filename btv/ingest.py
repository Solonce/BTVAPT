"""The ``scrape`` job: run a source adapter and persist what it found.

* Every response is archived raw (via ``Fetcher``).
* Each listing is upserted into ``source_listings``; a new immutable snapshot
  is written whenever its content differs from the latest one (unchanged
  observations only bump ``last_seen`` / ``last_verified``; the raw archive
  still holds every fetch).
* Status lifecycle: a listing absent from a *healthy* run of a complete feed
  accrues a miss; after ``gone_after_misses`` it is marked gone. A gone
  listing that reappears becomes ``relisted``.
"""

from __future__ import annotations

import hashlib
import json

from sqlalchemy.orm import Session

from btv import settings
from btv.db import session_factory, session_scope, utcnow
from btv.health import assess_run
from btv.http import PoliteClient
from btv.jobs.runner import JobContext, job
from btv.models import ListingSnapshot, ScrapeRun, Source, SourceListing, StatusEvent
from btv.sources.base import Fetcher, ParsedListing, get_adapter_class


def content_hash(pl: ParsedListing) -> str:
    blob = json.dumps(pl.content_fields(), sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def _set_status(session: Session, sl: SourceListing, new: str, run_id: int | None, reason: str) -> None:
    if sl.status == new:
        return
    session.add(StatusEvent(source_listing_id=sl.id, from_status=sl.status, to_status=new,
                            scrape_run_id=run_id, reason=reason))
    sl.status = new


def upsert_listing(session: Session, source: Source, run_id: int | None, pl: ParsedListing,
                   parser_version: str) -> tuple[SourceListing, bool]:
    """Returns (listing, snapshot_created)."""
    if not pl.url:
        raise ValueError(f"listing {pl.external_id!r} from {source.id} has no URL")
    now = utcnow()
    sl = (
        session.query(SourceListing)
        .filter_by(source_id=source.id, external_id=str(pl.external_id))
        .one_or_none()
    )
    if sl is None:
        sl = SourceListing(source_id=source.id, external_id=str(pl.external_id), url=pl.url,
                           first_seen=now, last_seen=now, last_verified=now, status="available")
        session.add(sl)
        session.flush()
        session.add(StatusEvent(source_listing_id=sl.id, from_status=None, to_status="available",
                                scrape_run_id=run_id, reason="first seen"))
    else:
        sl.url = pl.url
        sl.last_seen = sl.last_verified = now
    sl.consecutive_misses = 0

    wanted = "pending" if str(pl.extra.get("status", "")).lower() == "pending" else "available"
    if sl.status == "gone":
        _set_status(session, sl, "relisted", run_id, "reappeared after being gone")
    elif sl.status == "relisted" and wanted == "pending":
        _set_status(session, sl, "pending", run_id, "source marks pending")
    elif sl.status in ("available", "pending"):
        _set_status(session, sl, wanted, run_id, f"source reports {wanted}")

    h = content_hash(pl)
    latest = session.get(ListingSnapshot, sl.latest_snapshot_id) if sl.latest_snapshot_id else None
    created = False
    if latest is None or latest.content_hash != h:
        snap = ListingSnapshot(
            source_listing_id=sl.id, scrape_run_id=run_id, raw_fetch_id=pl.raw_fetch_id,
            observed_at=now, content_hash=h, parser_version=parser_version, **_snapshot_fields(pl),
        )
        session.add(snap)
        session.flush()
        sl.latest_snapshot_id = snap.id
        created = True
    return sl, created


def _snapshot_fields(pl: ParsedListing) -> dict:
    d = pl.content_fields()
    d.pop("external_id")
    return d


def apply_misses(session: Session, source: Source, run_id: int, seen: set[str]) -> dict:
    threshold = int(settings.get(session, "gone_after_misses"))
    now = utcnow()
    counts = {"missed": 0, "gone": 0}
    active = (
        session.query(SourceListing)
        .filter(SourceListing.source_id == source.id, SourceListing.status != "gone")
        .all()
    )
    for sl in active:
        if sl.external_id in seen:
            continue
        sl.consecutive_misses += 1
        sl.last_verified = now  # verified absent
        counts["missed"] += 1
        if sl.consecutive_misses >= threshold:
            _set_status(session, sl, "gone", run_id,
                        f"missing from {sl.consecutive_misses} consecutive healthy runs")
            counts["gone"] += 1
    return counts


@job("scrape")
def scrape(ctx: JobContext, source_id: str, **_ignored) -> dict:
    cfg = ctx.cfg
    Session_ = session_factory(cfg)
    session = Session_()
    client = None
    try:
        source = session.get(Source, source_id)
        if source is None:
            raise KeyError(f"unknown source {source_id!r}")
        # robots_override is an explicit per-source opt-out (see sources.toml).
        client = PoliteClient(cfg, respect_robots=not (source.config or {}).get("robots_override", False))
        if not source.enabled:
            ctx.log(f"{source_id} is disabled; skipping")
            return {"skipped": "disabled"}
        ctx.step(f"starting {source.name}")
        run = ScrapeRun(source_id=source.id, job_id=ctx.job_id, status="running")
        session.add(run)
        source.last_run_at = utcnow()
        session.commit()

        adapter = get_adapter_class(source.platform)(source, Fetcher(session, client, run.id))
        run.parser_version = adapter.parser_version
        seen: set[str] = set()
        created = 0
        try:
            for pl in adapter.run(ctx):
                _, new_snap = upsert_listing(session, source, run.id, pl, adapter.parser_version)
                created += int(new_snap)
                seen.add(str(pl.external_id))
                session.commit()
        except Exception as exc:
            session.rollback()
            run.status, run.error, run.finished_at, run.healthy = "failed", f"{type(exc).__name__}: {exc}", utcnow(), False
            run.listings_found = len(seen)
            source.last_status, source.last_error = "failed", run.error
            source.consecutive_failures += 1
            session.commit()
            raise

        ctx.step("health check")
        healthy, problems = assess_run(session, source, len(seen), exclude_run_id=run.id)
        run.status, run.finished_at, run.listings_found = "ok", utcnow(), len(seen)
        run.healthy, run.health_notes = healthy, "; ".join(problems) or None
        misses = {"missed": 0, "gone": 0}
        if healthy and adapter.complete_feed:
            ctx.step("recording misses")
            misses = apply_misses(session, source, run.id, seen)
        source.last_count = len(seen)
        source.consecutive_failures = 0
        if healthy:
            source.last_status, source.last_error, source.last_success_at = "ok", None, utcnow()
        else:
            source.last_status, source.last_error = "unhealthy", run.health_notes
            ctx.log(f"health check failed: {run.health_notes}", level="warn")
        session.commit()
        return {"scrape_run_id": run.id, "listings": len(seen), "new_snapshots": created,
                "healthy": healthy, "problems": problems, **misses}
    finally:
        if client is not None:
            client.close()
        session.close()


@job("scrape_due")
def scrape_due(ctx: JobContext, **_ignored) -> dict:
    """Enqueue a scrape job for every enabled source that is due."""
    from btv.health import next_due
    from btv.jobs.runner import enqueue

    queued = []
    with session_scope(ctx.cfg) as s:
        now = utcnow()
        due = [src.id for src in s.query(Source).filter(Source.enabled.is_(True)).all()
               if (nd := next_due(src)) is not None and nd <= now]
    for sid in due:
        enqueue("scrape", source_id=sid, cfg=ctx.cfg)
        queued.append(sid)
    return {"queued": queued}
