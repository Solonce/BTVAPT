"""Post-ingest processing for each new snapshot.

1. Availability text parsing (description beats structured field).
2. Canonical linking: source listing -> unit -> building, via normalized
   address. Every link change is written to ``merge_log`` so it can be
   audited and undone; user-locked links are never touched.
3. Contact extraction into the contact graph.

``rederive`` re-runs all of this over existing snapshots after parser
improvements (the raw archive + snapshots are the source of truth).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from btv.db import session_factory, utcnow
from btv.jobs.runner import JobContext, job
from btv.models import (
    Building,
    Company,
    Contact,
    ContactMention,
    ContactName,
    ContactPoint,
    ListingSnapshot,
    MergeLog,
    Source,
    SourceListing,
    Unit,
)
from btv.normalize.address import NormalizedAddress, normalize_address
from btv.normalize.contacts import (
    Mention,
    extract_mentions,
    format_phone,
    is_relay_email,
    name_similarity,
    normalize_name,
)
from btv.normalize.dates import parse_availability

AUTO_LINK_NAME = 0.9
REVIEW_NAME = 0.75


# ------------------------------------------------------------------ dates


def derive_availability(snap: ListingSnapshot) -> None:
    ref = (snap.observed_at or utcnow()).date()
    found = parse_availability(snap.description, ref) or parse_availability(snap.title, ref)
    if found is None:
        snap.avail_text_raw = snap.avail_text_date = snap.avail_text_kind = None
        snap.avail_text_confidence = snap.avail_text_method = None
        return
    snap.avail_text_raw = found.raw
    snap.avail_text_date = found.date
    snap.avail_text_kind = found.kind
    snap.avail_text_confidence = found.confidence
    snap.avail_text_method = found.method


# ---------------------------------------------------------------- linking


def resolve_unit(session: Session, unit_id: int | None) -> Unit | None:
    unit = session.get(Unit, unit_id) if unit_id else None
    seen = set()
    while unit is not None and unit.merged_into_id and unit.id not in seen:
        seen.add(unit.id)
        unit = session.get(Unit, unit.merged_into_id)
    return unit


def find_or_create_building(session: Session, na: NormalizedAddress, lat=None, lon=None) -> Building:
    key = na.building_key
    b = session.query(Building).filter_by(norm_key=key).one_or_none()
    if b is None:
        street_key = key.split("|")[0]
        if na.city:
            # An earlier city-less record of the same street address: adopt and complete it.
            b = session.query(Building).filter_by(norm_key=f"{street_key}||{na.state or ''}").one_or_none()
            if b is not None:
                b.norm_key, b.city = key, na.city
                b.display_address = na.display
        else:
            same = session.query(Building).filter(Building.norm_key.like(f"{street_key}|%|{na.state or ''}")).all()
            if len(same) == 1:
                b = same[0]
    if b is None:
        b = Building(norm_key=key, street_number=na.street_number, street=na.street, city=na.city,
                     state=na.state, zip=na.zip, display_address=na.display)
        session.add(b)
        session.flush()
    if b.zip is None and na.zip:
        b.zip = na.zip
    if b.lat is None and lat is not None and lon is not None:
        b.lat, b.lon, b.geocode_source, b.geocoded_at = float(lat), float(lon), "source", utcnow()
    return b


def find_or_create_unit(session: Session, building: Building, norm_unit: str, label: str | None) -> Unit:
    u = session.query(Unit).filter_by(building_id=building.id, norm_unit=norm_unit).one_or_none()
    if u is None:
        u = Unit(building_id=building.id, norm_unit=norm_unit, unit_label=label or (norm_unit or None))
        session.add(u)
        session.flush()
    return resolve_unit(session, u.id) or u


TRUSTED_LINK_PLATFORMS = ("buildium", "nesthub", "appfolio", "rentcafe")
APPROX_PREFIX = "~"


def _approximate_address(extra: dict) -> NormalizedAddress | None:
    """Posts with only a map pin (Craigslist without a street address) become an
    approximate 'building' at that point (~100 m grid), so they show on the map."""
    lat, lon = extra.get("lat"), extra.get("lon")
    if lat is None or lon is None:
        return None
    where = (extra.get("approx_location") or "").strip()
    label = f"Approximate location{' — ' + where if where else ''}"
    return NormalizedAddress(street_number=None, street=f"{APPROX_PREFIX}{float(lat):.3f},{float(lon):.3f}",
                             city=None, state="vt", zip=None, unit="", display=label)


def _match_manager_unit_by_text(session: Session, building: Building, snap: ListingSnapshot) -> Unit | None:
    """A unit-less repost (e.g. a manager syndicating to Craigslist) whose text
    matches a manager-site listing in the same building is the same unit."""
    from btv.normalize.scam import shingles

    mine = shingles(snap.description)
    if len(mine) < 8:
        return None
    best, best_j = None, 0.0
    rows = (session.query(SourceListing, ListingSnapshot, Source)
            .join(ListingSnapshot, ListingSnapshot.id == SourceListing.latest_snapshot_id)
            .join(Source, Source.id == SourceListing.source_id)
            .join(Unit, Unit.id == SourceListing.unit_id)
            .filter(Unit.building_id == building.id, Source.platform.in_(TRUSTED_LINK_PLATFORMS),
                    SourceListing.status != "gone").all())
    for other_sl, other_snap, _ in rows:
        theirs = shingles(other_snap.description)
        if not theirs:
            continue
        j = len(mine & theirs) / len(mine | theirs)
        if j > best_j:
            best, best_j = other_sl, j
    if best is not None and best_j >= 0.5:
        return resolve_unit(session, best.unit_id)
    return None


def link_listing(session: Session, sl: SourceListing, snap: ListingSnapshot, source: Source | None = None) -> Unit | None:
    if sl.link_locked:
        return resolve_unit(session, sl.unit_id)
    source = source or session.get(Source, sl.source_id)
    raw = sl.address_override or snap.address_raw
    na = normalize_address(raw, None if sl.address_override else snap.unit_raw,
                           default_city=(source.config or {}).get("default_city"))
    extra = snap.extra or {}
    if (na is None or not na.street) and not sl.address_override:
        approx = _approximate_address(extra)
        if approx is None:
            return resolve_unit(session, sl.unit_id)
        # Many posts share a neighbourhood pin: never merge them by location alone.
        approx.unit = f"post-{sl.id}"
        na = approx
    if na is None or not na.street:
        return resolve_unit(session, sl.unit_id)
    building = find_or_create_building(session, na, extra.get("lat"), extra.get("lon"))
    unit = None
    if not na.unit and source.platform not in TRUSTED_LINK_PLATFORMS:
        unit = _match_manager_unit_by_text(session, building, snap)
    unit = unit or find_or_create_unit(session, building, na.unit, snap.unit_raw)
    current = resolve_unit(session, sl.unit_id)
    if current is None or current.id != unit.id:
        session.add(MergeLog(
            action="link_listing",
            subject={"source_listing_id": sl.id, "from_unit_id": sl.unit_id, "to_unit_id": unit.id,
                     "building_key": na.building_key, "norm_unit": unit.norm_unit},
            reason=("text matches manager listing in same building" if unit.norm_unit != na.unit else "address match")
                   if current is None else "address changed",
            actor="auto",
        ))
        sl.unit_id = unit.id
    return unit


# --------------------------------------------------------------- contacts


def company_for_source(session: Session, source: Source) -> Company:
    c = session.query(Company).filter_by(source_id=source.id).first()
    if c is None:
        c = Company(name=source.name, norm_name=normalize_name(source.name) or source.id, kind="manager",
                    source_id=source.id, website=(source.config or {}).get("base_url"))
        session.add(c)
        session.flush()
    elif c.name != source.name:
        c.name, c.norm_name = source.name, normalize_name(source.name) or source.id
    return c


def _point(session: Session, kind: str, value: str) -> ContactPoint | None:
    return session.query(ContactPoint).filter_by(kind=kind, value=value).one_or_none()


PRIVATE_PLATFORMS = ("craigslist", "manual", "email")


def resolve_mention(session: Session, m: Mention, company: Company | None) -> tuple[Contact | None, str, int | None]:
    """Returns (contact, review_state, suggested_contact_id)."""
    if m.email and is_relay_email(m.email):
        if _point(session, "email", m.email) is None:
            session.add(ContactPoint(kind="email", value=m.email, company_id=company.id if company else None))
            session.flush()
        m.email = None
        if not (m.name or m.phone):
            return None, "auto", None

    contact = None
    for kind, value in (("email", m.email), ("phone", m.phone)):
        if value:
            cp = _point(session, kind, value)
            if cp is not None and cp.contact_id:
                contact = session.get(Contact, cp.contact_id)
                break
    review, suggested = "auto", None
    if contact is None and m.name:
        best, best_score = None, 0.0
        same_owner = Contact.company_id == company.id if company else Contact.company_id.is_(None)
        for cn in (session.query(ContactName).join(Contact, Contact.id == ContactName.contact_id)
                   .filter(same_owner, Contact.merged_into_id.is_(None)).all()):
            score = name_similarity(m.name, cn.name)
            if score > best_score:
                best, best_score = cn, score
        if best is not None and best_score >= AUTO_LINK_NAME:
            contact = session.get(Contact, best.contact_id)
        elif best is not None and best_score >= REVIEW_NAME:
            review, suggested = "needs_review", best.contact_id
    if contact is None:
        label = m.name or format_phone(m.phone) or m.email
        kind = "person" if m.name else ("office" if m.origin == "contact_block" else "unknown")
        contact = Contact(display_name=label, company_id=company.id if company else None, role=m.extra.get("role"),
                          kind=kind if company else "person")
        session.add(contact)
        session.flush()
    while contact.merged_into_id:
        contact = session.get(Contact, contact.merged_into_id)
    if m.name:
        norm = normalize_name(m.name)
        if norm and session.query(ContactName).filter_by(contact_id=contact.id, norm=norm).first() is None:
            session.add(ContactName(contact_id=contact.id, name=m.name, norm=norm))
        # Prefer a person's name over a phone-number label.
        if contact.display_name and contact.display_name.startswith(("(", "+")):
            contact.display_name = m.name
        if contact.kind != "person" and m.origin == "description":
            contact.kind = "person"
    for kind, value in (("email", m.email), ("phone", m.phone)):
        if value:
            cp = _point(session, kind, value)
            if cp is None:
                session.add(ContactPoint(kind=kind, value=value, contact_id=contact.id,
                                         company_id=company.id if company else None))
            elif cp.contact_id is None:
                cp.contact_id = contact.id
    session.flush()
    return contact, review, suggested


def extract_contacts(session: Session, sl: SourceListing, snap: ListingSnapshot, source: Source) -> int:
    company = None if source.platform in PRIVATE_PLATFORMS else company_for_source(session, source)
    session.query(ContactMention).filter_by(snapshot_id=snap.id).delete()
    n = 0
    for m in extract_mentions(snap.contact_raw, snap.description):
        contact, review, suggested = resolve_mention(session, m, company)
        session.add(ContactMention(
            snapshot_id=snap.id, source_listing_id=sl.id, contact_id=contact.id if contact else None,
            company_id=company.id if company else None, name_raw=m.name, phone=m.phone, email=m.email, origin=m.origin,
            confidence=m.confidence, review_state=review, suggested_contact_id=suggested,
        ))
        n += 1
    return n


# ------------------------------------------------------------------ entry


def process_snapshot(session: Session, sl: SourceListing, snap: ListingSnapshot, source: Source | None = None) -> None:
    source = source or session.get(Source, sl.source_id)
    derive_availability(snap)
    link_listing(session, sl, snap, source)
    extract_contacts(session, sl, snap, source)


@job("rederive")
def rederive(ctx: JobContext, **_ignored) -> dict:
    """Re-run date parsing, linking and contact extraction on latest snapshots."""
    session = session_factory(ctx.cfg)()
    try:
        rows = session.query(SourceListing).filter(SourceListing.latest_snapshot_id.isnot(None)).all()
        ctx.set_total(len(rows))
        for sl in rows:
            snap = session.get(ListingSnapshot, sl.latest_snapshot_id)
            process_snapshot(session, sl, snap)
            session.commit()
            ctx.advance(step=f"{snap.address_raw or sl.external_id}")
        return {"listings": len(rows)}
    finally:
        session.close()


def today() -> date:
    return utcnow().date()
