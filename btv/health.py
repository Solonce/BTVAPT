"""Per-source health: run-level checks and the dashboard state.

A scrape run is *healthy* only if it completed and its listing count looks
plausible. Unhealthy runs never count toward marking listings gone, so a
broken parser or a blocked request can't masquerade as units being rented.
"""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from btv.config import Config, get_config
from btv.db import utcnow
from btv.models import ScrapeRun, Source

DROP_RATIO = 0.5  # count below this fraction of recent median => suspicious
DROP_MIN_MEDIAN = 4  # don't apply the drop check to tiny feeds


def assess_run(session: Session, source: Source, count: int, exclude_run_id: int | None = None) -> tuple[bool, list[str]]:
    problems: list[str] = []
    if count < (source.expected_min or 0):
        problems.append(f"count {count} below expected minimum {source.expected_min}")
    if source.expected_max is not None and count > source.expected_max:
        problems.append(f"count {count} above expected maximum {source.expected_max}")
    q = (
        session.query(ScrapeRun.listings_found)
        .filter(ScrapeRun.source_id == source.id, ScrapeRun.healthy.is_(True))
    )
    if exclude_run_id is not None:
        q = q.filter(ScrapeRun.id != exclude_run_id)
    recent = [r[0] for r in q.order_by(ScrapeRun.id.desc()).limit(5).all() if r[0] is not None]
    if recent:
        med = statistics.median(recent)
        if med >= DROP_MIN_MEDIAN and count < DROP_RATIO * med:
            problems.append(f"sudden drop: {count} vs recent median {med:g}")
    return (not problems, problems)


def source_health(source: Source, cfg: Config | None = None, now: datetime | None = None) -> dict:
    cfg = cfg or get_config()
    now = now or utcnow()
    state = "ok"
    reasons: list[str] = []
    if not source.enabled:
        state = "disabled"
        reasons.append(source.disabled_reason or "kill switch off")
    elif source.last_run_at is None:
        state = "never_run"
    elif source.last_status == "failed":
        state = "failing"
        reasons.append(source.last_error or "last run failed")
    elif source.last_status == "unhealthy":
        state = "degraded"
        reasons.append(source.last_error or "last run failed health checks")
    if state in ("ok", "failing", "degraded") and source.last_run_at is not None:
        limit = timedelta(minutes=source.interval_minutes * cfg.stale_after_intervals)
        # Never succeeded yet: only stale once the first attempt is itself old.
        reference = source.last_success_at or source.last_run_at
        if now - reference > limit:
            if state == "ok":
                state = "stale"
            reasons.append(
                "no successful run since "
                + (source.last_success_at.isoformat() + "Z" if source.last_success_at else "ever")
            )
    return {
        "source_id": source.id,
        "name": source.name,
        "platform": source.platform,
        "enabled": source.enabled,
        "state": state,
        "reasons": reasons,
        "last_run_at": _iso(source.last_run_at),
        "last_success_at": _iso(source.last_success_at),
        "last_count": source.last_count,
        "expected_min": source.expected_min,
        "expected_max": source.expected_max,
        "consecutive_failures": source.consecutive_failures,
        "interval_minutes": source.interval_minutes,
        "robots_override": bool((source.config or {}).get("robots_override", False)),
        "next_due_at": _iso(next_due(source)),
    }


def next_due(source: Source) -> datetime | None:
    if not source.enabled:
        return None
    if source.last_run_at is None:
        return utcnow()
    # Back off after repeated failures: double the interval per failure, capped at 1 day.
    factor = 2 ** min(source.consecutive_failures, 4)
    minutes = min(source.interval_minutes * factor, 24 * 60)
    return source.last_run_at + timedelta(minutes=minutes)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt else None
