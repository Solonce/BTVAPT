"""Source adapter interface and registry.

A *platform* adapter (Buildium, AppFolio, ...) is generic; a *source* is one
company configured in ``config/sources.toml`` that uses a platform adapter
with its own settings (subdomain, expected counts, schedule).
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterator

from sqlalchemy.orm import Session

from btv.archive import record_fetch
from btv.db import utcnow
from btv.http import FetchResult, PoliteClient

if TYPE_CHECKING:
    from btv.jobs.runner import JobContext
    from btv.models import ListingSnapshot, Source


@dataclass
class ParsedListing:
    """What an adapter extracts for one listing. ``url`` is mandatory."""

    external_id: str
    url: str
    title: str | None = None
    address_raw: str | None = None
    unit_raw: str | None = None
    rent: int | None = None
    beds: float | None = None
    baths: float | None = None
    sqft: int | None = None
    avail_structured_raw: str | None = None
    avail_structured_date: date | None = None
    description: str | None = None
    pets: str | None = None
    parking: str | None = None
    utilities: str | None = None
    amenities: list[str] = field(default_factory=list)
    photos: list[str] = field(default_factory=list)
    contact_raw: dict | None = None
    extra: dict = field(default_factory=dict)
    raw_fetch_id: int | None = None

    def content_fields(self) -> dict:
        d = asdict(self)
        d.pop("raw_fetch_id")
        return d


class Fetcher:
    """PoliteClient wrapper that archives every response against a scrape run."""

    def __init__(self, session: Session, client: PoliteClient, scrape_run_id: int | None):
        self.session = session
        self.client = client
        self.scrape_run_id = scrape_run_id

    def get(self, url: str, **kw) -> tuple[FetchResult, int]:
        return self._do("GET", url, **kw)

    def post(self, url: str, **kw) -> tuple[FetchResult, int]:
        return self._do("POST", url, **kw)

    def _do(self, method: str, url: str, **kw) -> tuple[FetchResult, int]:
        res = self.client.request(method, url, **kw)
        fetch = record_fetch(self.session, url=res.url, body=res.body, http_status=res.status,
                             content_type=res.content_type, scrape_run_id=self.scrape_run_id,
                             method=method, cfg=self.client.cfg)
        self.session.commit()
        return res, fetch.id

    def record(self, url: str, body: bytes, content_type: str | None = None, via: str = "browser") -> int:
        """Archive content obtained some other way (browser, email, paste)."""
        fetch = record_fetch(self.session, url=url, body=body, http_status=None,
                             content_type=content_type, scrape_run_id=self.scrape_run_id,
                             via=via, cfg=self.client.cfg)
        self.session.commit()
        return fetch.id


class Adapter(ABC):
    platform: ClassVar[str]
    parser_version: ClassVar[str] = "0"
    # True when the feed lists every active unit, so absence counts as a miss.
    complete_feed: ClassVar[bool] = True

    def __init__(self, source: "Source", fetcher: Fetcher):
        self.source = source
        self.config = source.config or {}
        self.fetcher = fetcher

    @abstractmethod
    def run(self, ctx: "JobContext") -> Iterator[ParsedListing]:
        """Yield listings, reporting progress on ``ctx`` as pages/details load."""

    # -- helpers for incremental detail fetching -----------------------------
    def previous_snapshot(self, external_id: str) -> "ListingSnapshot | None":
        from btv.models import ListingSnapshot, SourceListing

        sl = (
            self.fetcher.session.query(SourceListing)
            .filter_by(source_id=self.source.id, external_id=str(external_id))
            .one_or_none()
        )
        if sl is None or sl.latest_snapshot_id is None:
            return None
        return self.fetcher.session.get(ListingSnapshot, sl.latest_snapshot_id)

    def last_fetched_at(self, url: str) -> datetime | None:
        from btv.models import RawFetch

        row = (
            self.fetcher.session.query(RawFetch.fetched_at)
            .filter(RawFetch.url == url, RawFetch.http_status == 200)
            .order_by(RawFetch.id.desc())
            .first()
        )
        return row[0] if row else None

    def detail_is_fresh(self, url: str, prev_summary: object, summary: object) -> bool:
        """True if the list-level record is unchanged and ``url`` was fetched recently."""
        if prev_summary is None or prev_summary != summary:
            return False
        last = self.last_fetched_at(url)
        hours = float(self.config.get("detail_refresh_hours", 24))
        return last is not None and utcnow() - last < timedelta(hours=hours)


PLATFORMS: dict[str, type[Adapter]] = {}


def register(cls: type[Adapter]) -> type[Adapter]:
    PLATFORMS[cls.platform] = cls
    return cls


def get_adapter_class(platform: str) -> type[Adapter]:
    import btv.sources.appfolio  # noqa: F401  (registers built-ins)
    import btv.sources.buildium  # noqa: F401
    import btv.sources.file  # noqa: F401
    import btv.sources.nesthub  # noqa: F401

    if platform not in PLATFORMS:
        raise KeyError(f"no adapter for platform {platform!r}; known: {sorted(PLATFORMS)}")
    return PLATFORMS[platform]


def parse_date(value) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def load_json_file(path: str | Path) -> object:
    return json.loads(Path(path).read_text())


def html_to_text(value: str | None) -> str | None:
    """Collapse simple listing HTML (``<br>``, ``<p>``, ``<li>``) to plain text."""
    import html as _html
    import re

    if value is None:
        return None
    text = re.sub(r"(?i)<br\s*/?>|</p>|</li>", "\n", value)
    text = re.sub(r"(?i)<li[^>]*>", "- ", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = _html.unescape(text).replace("\r\n", "\n")
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip() or None


def to_number(value, kind=float):
    if value in (None, "", False):
        return None
    try:
        n = float(str(value).replace("$", "").replace(",", "").strip())
    except ValueError:
        return None
    return kind(n) if kind is not int else int(round(n))
