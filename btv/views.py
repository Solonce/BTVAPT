"""Read models for the API/UI: units, contacts, companies.

Everything user-facing is computed at view time from stored observations,
so the target date and near-miss window are pure display settings.

Field conflicts between sources are resolved per field by source priority
(manager sites first) then recency; every source link is kept and shown.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy.orm import Session

from btv import settings as user_settings
from btv.db import utcnow
from btv.models import (
    Building,
    BuildingOwner,
    Company,
    Contact,
    ContactMention,
    ContactName,
    ContactPoint,
    ListingSnapshot,
    MergeLog,
    OutreachLog,
    Source,
    SourceListing,
    StatusEvent,
    Unit,
    UnitPrefs,
    UnitTag,
)
from btv.normalize.amenities import detect_amenities
from btv.normalize.contacts import format_phone
from btv.normalize.dates import classify, effective_availability
from btv.normalize.scam import ScamIndex, text_flags

PLATFORM_RANK = {"buildium": 0, "nesthub": 0, "appfolio": 0, "custom": 1, "craigslist": 2,
                 "aggregator": 3, "email": 3, "file": 4, "manual": 4}
STATUS_RANK = {"available": 0, "relisted": 1, "pending": 2, "gone": 3}
ACTIVE = ("available", "relisted", "pending")


def _iso(d) -> str | None:
    if d is None:
        return None
    return d.isoformat() + ("Z" if hasattr(d, "hour") else "")


class Ctx:
    """Bulk-loaded lookups so building many unit summaries stays cheap."""

    def __init__(self, s: Session):
        self.s = s
        self.today = utcnow().date()
        st = user_settings.get_all(s)
        self.target = date.fromisoformat(str(st["target_move_in"]))
        self.window = int(st["near_miss_days"])
        self.sources = {x.id: x for x in s.query(Source).all()}
        self.buildings = {b.id: b for b in s.query(Building).all()}
        self.units = {u.id: u for u in s.query(Unit).all()}
        self.listings_by_unit: dict[int, list[SourceListing]] = defaultdict(list)
        for sl in s.query(SourceListing).filter(SourceListing.unit_id.isnot(None)).all():
            self.listings_by_unit[self.resolve(sl.unit_id)].append(sl)
        snap_ids = [sl.latest_snapshot_id for ls in self.listings_by_unit.values() for sl in ls if sl.latest_snapshot_id]
        self.snaps = {x.id: x for x in s.query(ListingSnapshot).filter(ListingSnapshot.id.in_(snap_ids)).all()} if snap_ids else {}
        self.prefs = {p.unit_id: p for p in s.query(UnitPrefs).all()}
        self.tags: dict[int, list[str]] = defaultdict(list)
        for t in s.query(UnitTag).order_by(UnitTag.tag).all():
            self.tags[t.unit_id].append(t.tag)
        companies = {c.id: c for c in s.query(Company).all()}
        self.owners: dict[int, list[dict]] = defaultdict(list)
        for bo in s.query(BuildingOwner).all():
            co, mgr = companies.get(bo.company_id), companies.get(bo.manager_company_id)
            if co is not None:
                self.owners[bo.building_id].append({"company_id": co.id, "name": co.name, "kind": co.kind,
                                                    "manager": mgr.name if mgr else None,
                                                    "phone": format_phone(bo.phone), "source": bo.source})

    def resolve(self, unit_id: int) -> int:
        seen = set()
        u = self.units.get(unit_id)
        while u is not None and u.merged_into_id and u.id not in seen:
            seen.add(u.id)
            u = self.units.get(u.merged_into_id)
        return u.id if u else unit_id

    def rank(self, sl: SourceListing) -> tuple:
        src = self.sources.get(sl.source_id)
        return (STATUS_RANK.get(sl.status, 9), PLATFORM_RANK.get(src.platform if src else "", 5),
                -(sl.last_seen.timestamp() if sl.last_seen else 0))


def _pick(listings: list[tuple[SourceListing, ListingSnapshot]], field: str):
    for _, snap in listings:
        v = getattr(snap, field)
        if v not in (None, "", []):
            return v
    return None


def _availability(ctx: Ctx, pairs: list[tuple[SourceListing, ListingSnapshot]]) -> dict:
    # Prefer the most confident description-derived date among active listings.
    best = None
    for sl, snap in pairs:
        if snap.avail_text_kind in ("date", "now", "flexible"):
            conf = snap.avail_text_confidence or 0
            if best is None or conf > (best[1].avail_text_confidence or 0):
                best = (sl, snap)
    sl, snap = best or pairs[0]
    eff = effective_availability(snap.avail_structured_date, snap.avail_structured_raw,
                                 snap.avail_text_kind, snap.avail_text_date, ctx.today,
                                 snap.avail_text_confidence)
    cls = classify(eff, ctx.target, ctx.window)
    return {
        "effective_date": _iso(eff.date),
        "kind": eff.kind,
        "from": eff.source,
        "note": eff.note,
        "delta_days": cls["delta_days"],
        "match": cls["match"],
        "structured": {"raw": snap.avail_structured_raw, "date": _iso(snap.avail_structured_date)},
        "text": {"raw": snap.avail_text_raw, "date": _iso(snap.avail_text_date), "kind": snap.avail_text_kind,
                 "confidence": snap.avail_text_confidence, "method": snap.avail_text_method},
        "source_listing_id": sl.id,
    }


def unit_summary(ctx: Ctx, unit_id: int) -> dict | None:
    unit = ctx.units.get(unit_id)
    listings = ctx.listings_by_unit.get(unit_id) or []
    if unit is None or not listings:
        return None
    b = ctx.buildings.get(unit.building_id)
    listings = sorted(listings, key=ctx.rank)
    pairs = [(sl, ctx.snaps[sl.latest_snapshot_id]) for sl in listings if sl.latest_snapshot_id in ctx.snaps]
    if not pairs:
        return None
    active = [p for p in pairs if p[0].status in ACTIVE] or pairs
    status = listings[0].status
    structured_amen = []
    for _, snap in active:
        for a in snap.amenities or []:
            if a not in structured_amen:
                structured_amen.append(a)
    text_amen = detect_amenities(*(snap.description for _, snap in active[:2]), *(structured_amen))
    photos = _pick(active, "photos") or []
    prefs = ctx.prefs.get(unit_id)
    extra = active[0][1].extra or {}
    return {
        "id": unit_id,
        "building_id": unit.building_id,
        "address": b.display_address if b else None,
        "approximate": bool(b and (b.street or "").startswith("~")),
        "building_name": b.name if b else None,
        "city": (b.city or "").title() if b else None,
        "unit": None if unit.norm_unit.startswith("post-") else ((unit.unit_label or unit.norm_unit or "").lstrip("#").strip() or None),
        "lat": b.lat if b else None,
        "lon": b.lon if b else None,
        "status": status,
        "rent": _pick(active, "rent"),
        "beds": _pick(active, "beds"),
        "baths": _pick(active, "baths"),
        "sqft": _pick(active, "sqft"),
        "title": _pick(active, "title"),
        "pets": _pick(active, "pets"),
        "parking": _pick(active, "parking"),
        "utilities": _pick(active, "utilities"),
        "deposit": extra.get("deposit"),
        "amenities": structured_amen,
        "text_amenities": text_amen,
        "photo": photos[0] if photos else None,
        "photo_count": len(photos),
        "availability": _availability(ctx, active),
        "first_seen": _iso(min(sl.first_seen for sl in listings)),
        "last_verified": _iso(max(sl.last_verified for sl in listings)),
        "sources": [
            {"source_listing_id": sl.id, "source_id": sl.source_id,
             "source": ctx.sources[sl.source_id].name if sl.source_id in ctx.sources else sl.source_id,
             "platform": ctx.sources[sl.source_id].platform if sl.source_id in ctx.sources else None,
             "url": sl.url, "status": sl.status, "last_seen": _iso(sl.last_seen),
             "last_verified": _iso(sl.last_verified)}
            for sl in listings
        ],
        "owners": ctx.owners.get(unit.building_id, []),
        "private_landlord": any((snap.extra or {}).get("private_landlord") for _, snap in active)
                            and not any(ctx.sources.get(sl.source_id) and ctx.sources[sl.source_id].platform in
                                        ("buildium", "nesthub", "appfolio") for sl, _ in active),
        "posted_at": next(((snap.extra or {}).get("posted_at") for _, snap in active if (snap.extra or {}).get("posted_at")), None),
        "_description": active[0][1].description,
        "_photos": photos,
        "prefs": {"rating": prefs.rating if prefs else None, "status": prefs.status if prefs else None,
                  "notes": prefs.notes if prefs else None},
        "tags": ctx.tags.get(unit_id, []),
    }


def list_units(s: Session, include_gone: bool = False) -> dict:
    ctx = Ctx(s)
    allu = []
    for uid in ctx.listings_by_unit:
        if ctx.units.get(uid) and ctx.units[uid].merged_into_id:
            continue
        summ = unit_summary(ctx, uid)
        if summ:
            allu.append(summ)
    _add_risk(allu)
    out = [u for u in allu if include_gone or u["status"] != "gone"]
    out.sort(key=lambda u: (_match_order(u["availability"]["match"]), abs(u["availability"]["delta_days"] or 9999)))
    return {"target_move_in": ctx.target.isoformat(), "near_miss_days": ctx.window,
            "count": len(out), "units": out}


def _add_risk(units: list[dict]) -> None:
    idx = ScamIndex(units, {u["id"]: u["_description"] for u in units}, {u["id"]: u["_photos"] for u in units})
    for u in units:
        u["risk"] = idx.assess(u, u.pop("_description"), u.pop("_photos"))


def _match_order(m: str) -> int:
    return {"exact": 0, "near": 1, "late": 2, "early": 3, "unknown": 4}.get(m, 5)


def _public(u: dict | None) -> dict | None:
    if u is not None:
        u.pop("_description", None)
        u.pop("_photos", None)
    return u


def unit_detail(s: Session, unit_id: int) -> dict | None:
    ctx = Ctx(s)
    unit_id = ctx.resolve(unit_id)
    allu = [x for x in (unit_summary(ctx, uid) for uid in ctx.listings_by_unit
                        if not (ctx.units.get(uid) and ctx.units[uid].merged_into_id)) if x]
    _add_risk(allu)
    summ = next((x for x in allu if x["id"] == unit_id), None)
    if summ is None:
        return None
    listings = ctx.listings_by_unit[unit_id]
    sl_ids = [sl.id for sl in listings]
    snaps = (s.query(ListingSnapshot).filter(ListingSnapshot.source_listing_id.in_(sl_ids))
             .order_by(ListingSnapshot.observed_at).all())
    latest = [ctx.snaps[sl.latest_snapshot_id] for sl in sorted(listings, key=ctx.rank) if sl.latest_snapshot_id in ctx.snaps]
    history, last_rent = [], {}
    for sn in snaps:
        if sn.rent is not None and last_rent.get(sn.source_listing_id) != sn.rent:
            history.append({"at": _iso(sn.observed_at), "rent": sn.rent, "source_listing_id": sn.source_listing_id})
            last_rent[sn.source_listing_id] = sn.rent
    events = (s.query(StatusEvent).filter(StatusEvent.source_listing_id.in_(sl_ids))
              .order_by(StatusEvent.at).all())
    merges = (s.query(MergeLog).filter(MergeLog.action.in_(("merge_units", "link_listing", "unlink_listing"))).all())
    merges = [m for m in merges if unit_id in (m.subject.get("to_unit_id"), m.subject.get("from_unit_id"),
                                                m.subject.get("into_unit_id"), m.subject.get("merged_unit_id"))
              or m.subject.get("source_listing_id") in sl_ids]
    mentions = (s.query(ContactMention).filter(ContactMention.snapshot_id.in_([x.id for x in latest]),
                                              ContactMention.contact_id.isnot(None)).all())
    contact_ids = sorted({m.contact_id for m in mentions})
    b = ctx.buildings.get(summ["building_id"])
    siblings = [u.id for u in ctx.units.values() if u.building_id == summ["building_id"] and u.id != unit_id
                and not u.merged_into_id and u.id in ctx.listings_by_unit]
    return {
        **summ,
        "description": latest[0].description if latest else None,
        "photos": next((x.photos for x in latest if x.photos), []),
        "price_history": history,
        "status_events": [{"at": _iso(e.at), "source_listing_id": e.source_listing_id, "from": e.from_status,
                           "to": e.to_status, "reason": e.reason} for e in events],
        "merge_log": [{"id": m.id, "action": m.action, "subject": m.subject, "reason": m.reason, "actor": m.actor,
                       "at": _iso(m.at), "undone_at": _iso(m.undone_at)} for m in merges],
        "contacts": [contact_card(s, cid) for cid in contact_ids],
        "turnover": turnover(s, unit_id, listings, snaps),
        "building": {"id": b.id, "address": b.display_address, "geocode_source": b.geocode_source,
                     "other_units": siblings} if b else None,
        "listing_details": [
            {"source_listing_id": sl.id, "source_id": sl.source_id, "url": sl.url, "status": sl.status,
             "external_id": sl.external_id, "first_seen": _iso(sl.first_seen), "last_seen": _iso(sl.last_seen),
             "last_verified": _iso(sl.last_verified), "link_locked": sl.link_locked,
             "snapshot": _snap_dict(ctx.snaps.get(sl.latest_snapshot_id))}
            for sl in sorted(listings, key=ctx.rank)
        ],
    }


def _snap_dict(sn: ListingSnapshot | None) -> dict | None:
    if sn is None:
        return None
    return {"observed_at": _iso(sn.observed_at), "title": sn.title, "address_raw": sn.address_raw,
            "unit_raw": sn.unit_raw, "rent": sn.rent, "beds": sn.beds, "baths": sn.baths, "sqft": sn.sqft,
            "avail_structured_raw": sn.avail_structured_raw, "avail_text_raw": sn.avail_text_raw,
            "pets": sn.pets, "parking": sn.parking, "utilities": sn.utilities, "amenities": sn.amenities,
            "contact_raw": sn.contact_raw}


def turnover(s: Session, unit_id: int, listings: list[SourceListing], snaps: list[ListingSnapshot]) -> dict:
    """Past listing periods and a naive 12-month-lease prediction."""
    periods = []
    for sl in listings:
        gone = (s.query(StatusEvent).filter_by(source_listing_id=sl.id, to_status="gone")
                .order_by(StatusEvent.at.desc()).first())
        avail = next((x.avail_text_date or x.avail_structured_date for x in reversed(snaps)
                      if x.source_listing_id == sl.id and (x.avail_text_date or x.avail_structured_date)), None)
        periods.append({"source_listing_id": sl.id, "listed": _iso(sl.first_seen),
                        "off_market": _iso(gone.at) if gone else None, "available": _iso(avail)})
    anchors = [date.fromisoformat(p["available"]) for p in periods if p["available"]]
    predicted = None
    if anchors:
        a = max(anchors)
        try:
            predicted = a.replace(year=a.year + 1)
        except ValueError:
            predicted = a + timedelta(days=365)
    return {"periods": periods, "predicted_next_available": _iso(predicted),
            "basis": "12-month lease from last known availability" if predicted else None}


# --------------------------------------------------------------- contacts


def contact_card(s: Session, contact_id: int) -> dict:
    c = s.get(Contact, contact_id)
    points = s.query(ContactPoint).filter_by(contact_id=contact_id).all()
    company = s.get(Company, c.company_id) if c.company_id else None
    name = c.display_name
    if company and name and (name.startswith(("(", "+")) or "@" in name):
        name = f"{company.name} {'office' if name.startswith(('(', '+')) else 'email'}"
    return {
        "id": c.id, "name": name, "role": c.role, "kind": c.kind or ("office" if company else "person"),
        "private": company is None,
        "company": company.name if company else None, "company_id": c.company_id,
        "phones": [format_phone(p.value) for p in points if p.kind == "phone"],
        "emails": [p.value for p in points if p.kind == "email"],
    }


def _contact_units(s: Session, contact_ids: list[int]) -> dict[int, dict]:
    """contact_id -> {units, buildings, sources, rents, first/last seen, months}."""
    rows = (s.query(ContactMention.contact_id, SourceListing, ListingSnapshot)
            .join(ListingSnapshot, ListingSnapshot.id == ContactMention.snapshot_id)
            .join(SourceListing, SourceListing.id == ListingSnapshot.source_listing_id)
            .filter(ContactMention.contact_id.in_(contact_ids)).all())
    units = {u.id: u for u in s.query(Unit).all()}

    def resolve(uid):
        u = units.get(uid)
        while u is not None and u.merged_into_id:
            u = units.get(u.merged_into_id)
        return u

    agg: dict[int, dict] = defaultdict(lambda: {"units": set(), "buildings": set(), "sources": set(), "rents": [],
                                                "first": None, "last": None, "months": defaultdict(int),
                                                "listings": set(), "active": set()})
    for cid, sl, sn in rows:
        a = agg[cid]
        u = resolve(sl.unit_id)
        if u:
            a["units"].add(u.id)
            a["buildings"].add(u.building_id)
            if sl.status in ACTIVE:
                a["active"].add(u.id)
        a["sources"].add(sl.source_id)
        a["listings"].add(sl.id)
        if sn.rent:
            a["rents"].append(sn.rent)
        a["first"] = min(filter(None, (a["first"], sl.first_seen)))
        a["last"] = max(filter(None, (a["last"], sl.last_seen)))
        a["months"][sl.first_seen.strftime("%b")] += 1
    return agg


def list_contacts(s: Session) -> list[dict]:
    contacts = s.query(Contact).filter(Contact.merged_into_id.is_(None)).all()
    agg = _contact_units(s, [c.id for c in contacts])
    review = defaultdict(int)
    for m in s.query(ContactMention).filter_by(review_state="needs_review").all():
        review[m.contact_id] += 1
    outreach = defaultdict(int)
    for o in s.query(OutreachLog).all():
        outreach[o.contact_id] += 1
    out = []
    for c in contacts:
        a = agg.get(c.id)
        if a is None and not outreach.get(c.id) and not c.notes:
            continue  # orphaned by re-parsing; nothing links to it anymore
        card = contact_card(s, c.id)
        card.update({
            "unit_count": len(a["units"]) if a else 0,
            "building_count": len(a["buildings"]) if a else 0,
            "active_units": len(a["active"]) if a else 0,
            "listing_count": len(a["listings"]) if a else 0,
            "sources": sorted(a["sources"]) if a else [],
            "rent_min": min(a["rents"]) if a and a["rents"] else None,
            "rent_max": max(a["rents"]) if a and a["rents"] else None,
            "first_seen": _iso(a["first"]) if a else None,
            "last_seen": _iso(a["last"]) if a else None,
            "multi_property": bool(a and len(a["buildings"]) > 1),
            "needs_review": review.get(c.id, 0),
            "outreach_count": outreach.get(c.id, 0),
            "notes": c.notes,
        })
        out.append(card)
    out.sort(key=lambda x: (-x["building_count"], -x["unit_count"], x["name"] or ""))
    return out


def contact_detail(s: Session, contact_id: int) -> dict | None:
    c = s.get(Contact, contact_id)
    if c is None:
        return None
    while c.merged_into_id:
        c = s.get(Contact, c.merged_into_id)
    a = _contact_units(s, [c.id]).get(c.id)
    card = contact_card(s, c.id)
    ctx = Ctx(s)
    units = [_public(unit_summary(ctx, uid)) for uid in sorted(a["units"])] if a else []
    names = [n.name for n in s.query(ContactName).filter_by(contact_id=c.id).all()]
    mentions = (s.query(ContactMention).filter(ContactMention.contact_id == c.id).order_by(ContactMention.id.desc()).limit(50).all())
    suggestions = []
    for m in s.query(ContactMention).filter_by(contact_id=c.id, review_state="needs_review").all():
        if m.suggested_contact_id:
            suggestions.append({"mention_id": m.id, "name": m.name_raw,
                                "suggested": contact_card(s, m.suggested_contact_id)})
    outreach = s.query(OutreachLog).filter_by(contact_id=c.id).order_by(OutreachLog.at.desc()).all()
    return {
        **card,
        "notes": c.notes,
        "name_variants": names,
        "units": [u for u in units if u],
        "stats": {
            "unit_count": len(a["units"]) if a else 0,
            "building_count": len(a["buildings"]) if a else 0,
            "rent_min": min(a["rents"]) if a and a["rents"] else None,
            "rent_max": max(a["rents"]) if a and a["rents"] else None,
            "listing_months": dict(a["months"]) if a else {},
            "sources": sorted(a["sources"]) if a else [],
        },
        "mentions": [{"id": m.id, "origin": m.origin, "name": m.name_raw, "phone": format_phone(m.phone),
                      "email": m.email, "confidence": m.confidence, "review_state": m.review_state,
                      "source_listing_id": m.source_listing_id} for m in mentions],
        "merge_suggestions": suggestions,
        "outreach": [{"id": o.id, "at": _iso(o.at), "channel": o.channel, "direction": o.direction,
                      "summary": o.summary, "outcome": o.outcome, "follow_up_on": _iso(o.follow_up_on),
                      "unit_id": o.unit_id} for o in outreach],
    }


def list_companies(s: Session) -> list[dict]:
    out = []
    contacts = s.query(Contact).filter(Contact.merged_into_id.is_(None)).all()
    by_company = defaultdict(list)
    for c in contacts:
        by_company[c.company_id].append(c.id)
    for co in s.query(Company).filter(Company.merged_into_id.is_(None)).all():
        ids = by_company.get(co.id, [])
        agg = _contact_units(s, ids) if ids else {}
        units = set().union(*(a["units"] for a in agg.values())) if agg else set()
        rents = [r for a in agg.values() for r in a["rents"]]
        relay = [p.value for p in s.query(ContactPoint).filter_by(company_id=co.id, contact_id=None).all()]
        out.append({"id": co.id, "name": co.name, "kind": co.kind, "source_id": co.source_id,
                    "website": co.website, "contact_count": len(ids), "unit_count": len(units),
                    "rent_min": min(rents) if rents else None, "rent_max": max(rents) if rents else None,
                    "relay_addresses": relay})
    out.sort(key=lambda x: -x["unit_count"])
    return out


# ------------------------------------------------------------ owners / leads


def list_owners(s: Session) -> list[dict]:
    """Owner entities (LLCs / people) behind buildings, with what they own."""
    ctx = Ctx(s)
    units_by_building: dict[int, list[int]] = defaultdict(list)
    for uid, u in ctx.units.items():
        if not u.merged_into_id and uid in ctx.listings_by_unit:
            units_by_building[u.building_id].append(uid)
    out: dict[int, dict] = {}
    companies = {c.id: c for c in s.query(Company).all()}
    for bo in s.query(BuildingOwner).all():
        co = companies.get(bo.company_id)
        if co is None:
            continue
        o = out.setdefault(co.id, {"id": co.id, "name": co.name, "kind": co.kind, "phones": set(),
                                    "managers": set(), "buildings": [], "unit_ids": [], "notes": co.notes})
        b = ctx.buildings.get(bo.building_id)
        mgr = companies.get(bo.manager_company_id)
        if mgr:
            o["managers"].add(mgr.name)
        if bo.phone:
            o["phones"].add(format_phone(bo.phone))
        if b:
            o["buildings"].append({"id": b.id, "address": b.display_address, "lat": b.lat, "lon": b.lon,
                                   "listing_url": (bo.evidence or {}).get("listing_url"),
                                   "units_listed": (bo.evidence or {}).get("available_units"),
                                   "unit_ids": units_by_building.get(b.id, [])})
            o["unit_ids"] += units_by_building.get(b.id, [])
    rows = []
    for o in out.values():
        o["phones"], o["managers"] = sorted(o["phones"]), sorted(o["managers"])
        o["building_count"], o["unit_count"] = len(o["buildings"]), len(set(o["unit_ids"]))
        rows.append(o)
    rows.sort(key=lambda x: (-x["building_count"], x["name"]))
    return rows


def list_leads(s: Session) -> list[dict]:
    """Posts from people (bookmarklet / email / Craigslist without address) for triage."""
    sources = {x.id: x for x in s.query(Source).all()}
    lead_sources = [sid for sid, src in sources.items() if src.platform in ("manual", "email")]
    q = s.query(SourceListing).filter(
        (SourceListing.source_id.in_(lead_sources)) | (SourceListing.unit_id.is_(None)))
    rows = []
    for sl in q.order_by(SourceListing.first_seen.desc()).all():
        snap = s.get(ListingSnapshot, sl.latest_snapshot_id) if sl.latest_snapshot_id else None
        if snap is None:
            continue
        flags = text_flags(f"{snap.title or ''}\n{snap.description or ''}")
        score = sum(p for p, _, _ in flags)
        rows.append({
            "source_listing_id": sl.id, "source_id": sl.source_id,
            "source": sources[sl.source_id].name if sl.source_id in sources else sl.source_id,
            "url": sl.url if not sl.url.startswith("btv:") else None, "status": sl.status,
            "triage": sl.triage or "new", "unit_id": sl.unit_id, "address_override": sl.address_override,
            "title": snap.title, "text": snap.description, "rent": snap.rent, "beds": snap.beds,
            "address": snap.address_raw, "author": (snap.extra or {}).get("author"),
            "received_at": (snap.extra or {}).get("received_at") or _iso(sl.first_seen),
            "availability": {"raw": snap.avail_text_raw, "date": _iso(snap.avail_text_date), "kind": snap.avail_text_kind},
            "risk": {"level": "high" if score >= 5 else "medium" if score >= 3 else "low" if score else "none",
                     "score": score, "reasons": [{"points": p, "reason": r, "evidence": e} for p, r, e in flags]},
        })
    return rows
