"""Database schema.

Layers:
  * Operations: sources, jobs, scrape runs, raw fetch archive.
  * Observations: source listings (one per source + external id) and their
    immutable snapshots (one per scrape that saw the listing).
  * Canonical: buildings and units that source listings roll up to, with an
    auditable, reversible merge log and unit redirects.
  * Contacts: companies, contacts, name variants, phone/email points,
    mentions extracted from snapshots, outreach log.
  * User layer: rating/status/notes/tags keyed on unit id (follows redirects).
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from btv.db import Base, utcnow

# ---------------------------------------------------------------- operations


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[object] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # slug, e.g. "fiveseasons"
    name: Mapped[str] = mapped_column(String(200))
    platform: Mapped[str] = mapped_column(String(32))  # buildium | appfolio | custom | ...
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)  # kill switch
    disabled_reason: Mapped[str | None] = mapped_column(Text)
    interval_minutes: Mapped[int] = mapped_column(Integer, default=180)
    expected_min: Mapped[int] = mapped_column(Integer, default=0)
    expected_max: Mapped[int | None] = mapped_column(Integer)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_status: Mapped[str | None] = mapped_column(String(32))
    last_count: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(64))  # scrape | recheck | backup | reparse | ...
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    source_id: Mapped[str | None] = mapped_column(ForeignKey("sources.id"))
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    # queued | running | succeeded | failed | cancelled | interrupted
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime)
    total: Mapped[int | None] = mapped_column(Integer)
    done: Mapped[int] = mapped_column(Integer, default=0)
    step: Mapped[str | None] = mapped_column(Text)
    message: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict | None] = mapped_column(JSON)
    pid: Mapped[int | None] = mapped_column(Integer)

    events: Mapped[list["JobEvent"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="JobEvent.id"
    )

    @property
    def percent(self) -> float | None:
        if self.status == "succeeded":
            return 100.0
        if not self.total:
            return None
        return round(min(100.0, 100.0 * self.done / self.total), 1)


class JobEvent(Base):
    __tablename__ = "job_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    level: Mapped[str] = mapped_column(String(8), default="info")
    message: Mapped[str] = mapped_column(Text)
    job: Mapped[Job] = relationship(back_populates="events")


class ScrapeRun(Base):
    __tablename__ = "scrape_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|ok|failed
    listings_found: Mapped[int | None] = mapped_column(Integer)
    # Only runs that pass the health check may count toward "gone" misses.
    healthy: Mapped[bool | None] = mapped_column(Boolean)
    health_notes: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    parser_version: Mapped[str | None] = mapped_column(String(32))


class RawBlob(Base):
    """Content-addressed raw payload stored gzipped under the archive dir."""

    __tablename__ = "raw_blobs"
    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    path: Mapped[str] = mapped_column(Text)
    size: Mapped[int] = mapped_column(Integer)
    content_type: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RawFetch(Base):
    __tablename__ = "raw_fetches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scrape_run_id: Mapped[int | None] = mapped_column(ForeignKey("scrape_runs.id"), index=True)
    url: Mapped[str] = mapped_column(Text)
    method: Mapped[str] = mapped_column(String(8), default="GET")
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    http_status: Mapped[int | None] = mapped_column(Integer)
    blob_sha256: Mapped[str | None] = mapped_column(ForeignKey("raw_blobs.sha256"))
    via: Mapped[str] = mapped_column(String(16), default="http")  # http | browser | email | manual


# -------------------------------------------------------------- canonical


class Building(Base):
    __tablename__ = "buildings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    norm_key: Mapped[str] = mapped_column(String(200), unique=True)  # "46 lafountain st|burlington|vt"
    street_number: Mapped[str | None] = mapped_column(String(20))
    street: Mapped[str | None] = mapped_column(String(120))
    city: Mapped[str | None] = mapped_column(String(80))
    state: Mapped[str | None] = mapped_column(String(2))
    zip: Mapped[str | None] = mapped_column(String(10))
    display_address: Mapped[str] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(String(200))  # e.g. "247 Pearl"
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    geocode_source: Mapped[str | None] = mapped_column(String(32))
    geocoded_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    units: Mapped[list["Unit"]] = relationship(back_populates="building")


class Unit(Base):
    __tablename__ = "units"
    __table_args__ = (UniqueConstraint("building_id", "norm_unit"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    building_id: Mapped[int] = mapped_column(ForeignKey("buildings.id"), index=True)
    norm_unit: Mapped[str] = mapped_column(String(40), default="")  # "" = whole house / unknown
    unit_label: Mapped[str | None] = mapped_column(String(80))
    merged_into_id: Mapped[int | None] = mapped_column(ForeignKey("units.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    building: Mapped[Building] = relationship(back_populates="units")


class MergeLog(Base):
    """Every link/merge/split decision, reversible via ``undone_at``."""

    __tablename__ = "merge_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    action: Mapped[str] = mapped_column(String(32))  # link_listing | merge_units | merge_contacts | ...
    subject: Mapped[dict] = mapped_column(JSON)  # ids involved + prior state needed to undo
    reason: Mapped[str | None] = mapped_column(Text)
    score: Mapped[float | None] = mapped_column(Float)
    actor: Mapped[str] = mapped_column(String(16), default="auto")  # auto | user
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime)


# ----------------------------------------------------------- observations


class SourceListing(Base):
    __tablename__ = "source_listings"
    __table_args__ = (UniqueConstraint("source_id", "external_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(Text)
    unit_id: Mapped[int | None] = mapped_column(ForeignKey("units.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="available")
    # available | pending | gone | relisted
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_verified: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    consecutive_misses: Mapped[int] = mapped_column(Integer, default=0)
    # Set when the user links/unlinks by hand; auto-linking then leaves it alone.
    link_locked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # User-supplied address for leads whose source gave none (e.g. a Facebook post).
    address_override: Mapped[str | None] = mapped_column(Text)
    # Inbox triage for leads: new | saved | contacted | dismissed
    triage: Mapped[str | None] = mapped_column(String(16))
    latest_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("listing_snapshots.id", use_alter=True, name="fk_sl_latest_snapshot")
    )


class ListingSnapshot(Base):
    """Immutable record of what a source said about a listing at one moment."""

    __tablename__ = "listing_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_listing_id: Mapped[int] = mapped_column(ForeignKey("source_listings.id"), index=True)
    scrape_run_id: Mapped[int | None] = mapped_column(ForeignKey("scrape_runs.id"), index=True)
    raw_fetch_id: Mapped[int | None] = mapped_column(ForeignKey("raw_fetches.id"))
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    content_hash: Mapped[str] = mapped_column(String(64))  # of normalized fields, detects edits
    parser_version: Mapped[str | None] = mapped_column(String(32))
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    address_raw: Mapped[str | None] = mapped_column(Text)
    unit_raw: Mapped[str | None] = mapped_column(String(80))
    rent: Mapped[int | None] = mapped_column(Integer)  # dollars / month
    beds: Mapped[float | None] = mapped_column(Float)
    baths: Mapped[float | None] = mapped_column(Float)
    sqft: Mapped[int | None] = mapped_column(Integer)
    avail_structured_raw: Mapped[str | None] = mapped_column(Text)
    avail_structured_date: Mapped[date | None] = mapped_column(Date)
    avail_text_raw: Mapped[str | None] = mapped_column(Text)
    avail_text_date: Mapped[date | None] = mapped_column(Date)
    avail_text_kind: Mapped[str | None] = mapped_column(String(16))  # date | now | flexible | vague
    avail_text_confidence: Mapped[float | None] = mapped_column(Float)
    avail_text_method: Mapped[str | None] = mapped_column(String(16))  # regex | llm
    pets: Mapped[str | None] = mapped_column(Text)
    parking: Mapped[str | None] = mapped_column(Text)
    utilities: Mapped[str | None] = mapped_column(Text)
    amenities: Mapped[list] = mapped_column(JSON, default=list)
    photos: Mapped[list] = mapped_column(JSON, default=list)
    description: Mapped[str | None] = mapped_column(Text)
    contact_raw: Mapped[dict | None] = mapped_column(JSON)
    extra: Mapped[dict] = mapped_column(JSON, default=dict)  # anything else the source gave


Index("ix_snap_listing_observed", ListingSnapshot.source_listing_id, ListingSnapshot.observed_at)


class StatusEvent(Base):
    __tablename__ = "status_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_listing_id: Mapped[int] = mapped_column(ForeignKey("source_listings.id"), index=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    from_status: Mapped[str | None] = mapped_column(String(16))
    to_status: Mapped[str] = mapped_column(String(16))
    scrape_run_id: Mapped[int | None] = mapped_column(ForeignKey("scrape_runs.id"))
    reason: Mapped[str | None] = mapped_column(Text)


# --------------------------------------------------------------- contacts


class Company(Base):
    __tablename__ = "companies"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    norm_name: Mapped[str] = mapped_column(String(200), index=True)
    kind: Mapped[str | None] = mapped_column(String(32))  # manager | owner_llc | landlord
    website: Mapped[str | None] = mapped_column(Text)
    source_id: Mapped[str | None] = mapped_column(ForeignKey("sources.id"))
    notes: Mapped[str | None] = mapped_column(Text)
    merged_into_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"))


class Contact(Base):
    __tablename__ = "contacts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    display_name: Mapped[str | None] = mapped_column(String(200))
    kind: Mapped[str | None] = mapped_column(String(16))  # person | office | unknown
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"))
    role: Mapped[str | None] = mapped_column(String(64))
    notes: Mapped[str | None] = mapped_column(Text)
    merged_into_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ContactName(Base):
    __tablename__ = "contact_names"
    __table_args__ = (UniqueConstraint("contact_id", "norm"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    norm: Mapped[str] = mapped_column(String(200), index=True)


class ContactPoint(Base):
    __tablename__ = "contact_points"
    __table_args__ = (UniqueConstraint("kind", "value"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id"), index=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), index=True)
    kind: Mapped[str] = mapped_column(String(8))  # phone | email
    value: Mapped[str] = mapped_column(String(200))  # E.164 phone / lowercased email


class ContactMention(Base):
    """A name/phone/email seen in one snapshot; the evidence behind the graph."""

    __tablename__ = "contact_mentions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_listing_id: Mapped[int | None] = mapped_column(ForeignKey("source_listings.id"), index=True)
    suggested_contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id"))
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("listing_snapshots.id"), index=True)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id"), index=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), index=True)
    name_raw: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(200))
    origin: Mapped[str] = mapped_column(String(16))  # contact_block | description
    confidence: Mapped[float | None] = mapped_column(Float)
    review_state: Mapped[str] = mapped_column(String(16), default="auto")  # auto | needs_review | confirmed | rejected


class BuildingOwner(Base):
    """Owner entity (LLC or person) behind a building, from public listing data."""

    __tablename__ = "building_owners"
    __table_args__ = (UniqueConstraint("building_id", "company_id", "source"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    building_id: Mapped[int] = mapped_column(ForeignKey("buildings.id"), index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    manager_company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"))
    source: Mapped[str] = mapped_column(String(32))  # showmetherent | manual | parcel
    phone: Mapped[str | None] = mapped_column(String(32))
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    evidence: Mapped[dict | None] = mapped_column(JSON)


class OutreachLog(Base):
    __tablename__ = "outreach_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id"), index=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), index=True)
    unit_id: Mapped[int | None] = mapped_column(ForeignKey("units.id"))
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    channel: Mapped[str] = mapped_column(String(16))  # email | call | text | in_person
    direction: Mapped[str] = mapped_column(String(8), default="out")  # out | in
    summary: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str | None] = mapped_column(Text)
    follow_up_on: Mapped[date | None] = mapped_column(Date)


# ------------------------------------------------------------- user layer


class UnitPrefs(Base):
    __tablename__ = "unit_prefs"
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id"), primary_key=True)
    rating: Mapped[int | None] = mapped_column(Integer)  # 1-5 stars
    status: Mapped[str | None] = mapped_column(String(16))  # interested | toured | applied | passed
    notes: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class UnitTag(Base):
    __tablename__ = "unit_tags"
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id"), primary_key=True)
    tag: Mapped[str] = mapped_column(String(64), primary_key=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
