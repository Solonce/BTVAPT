"""Content-addressed raw payload archive.

Every fetched page/JSON body is gzipped to ``archive/ab/cd/<sha256>.gz`` and
recorded as a ``RawBlob``; each fetch gets a ``RawFetch`` row pointing at it,
so identical payloads are stored once but every observation is kept.
"""

from __future__ import annotations

import gzip
import hashlib
import os
from pathlib import Path

from sqlalchemy.orm import Session

from btv.config import Config, get_config
from btv.models import RawBlob, RawFetch


def _blob_path(cfg: Config, sha: str) -> Path:
    return cfg.archive_dir / sha[:2] / sha[2:4] / f"{sha}.gz"


def store_blob(session: Session, body: bytes, content_type: str | None = None,
               cfg: Config | None = None) -> str:
    cfg = cfg or get_config()
    sha = hashlib.sha256(body).hexdigest()
    if session.get(RawBlob, sha) is None:
        path = _blob_path(cfg, sha)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            with gzip.open(tmp, "wb") as fh:
                fh.write(body)
            os.replace(tmp, path)
        session.add(RawBlob(sha256=sha, path=str(path.relative_to(cfg.archive_dir)),
                            size=len(body), content_type=content_type))
        session.flush()
    return sha


def record_fetch(session: Session, *, url: str, body: bytes | None, http_status: int | None,
                 content_type: str | None = None, scrape_run_id: int | None = None,
                 method: str = "GET", via: str = "http", cfg: Config | None = None) -> RawFetch:
    sha = store_blob(session, body, content_type, cfg) if body is not None else None
    fetch = RawFetch(url=url, method=method, http_status=http_status, blob_sha256=sha,
                     scrape_run_id=scrape_run_id, via=via)
    session.add(fetch)
    session.flush()
    return fetch


def load_blob(sha: str, cfg: Config | None = None) -> bytes:
    cfg = cfg or get_config()
    with gzip.open(_blob_path(cfg, sha), "rb") as fh:
        return fh.read()
