"""Sync ``config/sources.toml`` into the ``sources`` table.

The TOML file is the source of truth for configuration (platform, URLs,
schedule, expected counts). The kill switch (``enabled``) is taken from TOML
only when a source is first created; afterwards it is controlled at runtime
via ``btv sources disable/enable`` or the API, so flipping it never requires
editing files.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from sqlalchemy.orm import Session

from btv.models import Source

_COLUMNS = ("name", "platform", "interval_minutes", "expected_min", "expected_max")


def load_sources_file(path: Path) -> dict[str, dict]:
    data = tomllib.loads(path.read_text())
    return data.get("sources", {})


def sync_sources(session: Session, path: Path) -> dict[str, list[str]]:
    report: dict[str, list[str]] = {"created": [], "updated": [], "orphaned": []}
    defs = load_sources_file(path)
    for sid, d in defs.items():
        d = dict(d)
        cols = {k: d.pop(k) for k in _COLUMNS if k in d}
        enabled = d.pop("enabled", True)
        disabled_reason = d.pop("disabled_reason", None)
        src = session.get(Source, sid)
        if src is None:
            session.add(Source(id=sid, config=d, enabled=enabled,
                               disabled_reason=None if enabled else disabled_reason, **cols))
            report["created"].append(sid)
        else:
            changed = src.config != d or any(getattr(src, k) != v for k, v in cols.items())
            src.config = d
            for k, v in cols.items():
                setattr(src, k, v)
            if changed:
                report["updated"].append(sid)
    for src in session.query(Source).all():
        if src.id not in defs:
            report["orphaned"].append(src.id)
    return report


def set_enabled(session: Session, source_id: str, enabled: bool, reason: str | None = None) -> Source:
    src = session.get(Source, source_id)
    if src is None:
        raise KeyError(f"unknown source {source_id!r}")
    src.enabled = enabled
    src.disabled_reason = None if enabled else (reason or "disabled manually")
    return src
