"""User actions: notes/tags, auditable merges and splits, outreach log.

Every structural change writes a ``MergeLog`` row whose ``subject`` holds
what's needed to undo it; ``undo(merge_id)`` reverses it.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from btv.db import utcnow
from btv.models import (
    Contact,
    ContactMention,
    ContactName,
    ContactPoint,
    MergeLog,
    OutreachLog,
    SourceListing,
    Unit,
    UnitPrefs,
    UnitTag,
)
from btv.pipeline import resolve_unit

PREF_STATUSES = {None, "interested", "toured", "applied", "passed"}


class ActionError(ValueError):
    pass


def _unit(s: Session, unit_id: int) -> Unit:
    u = resolve_unit(s, unit_id)
    if u is None:
        raise ActionError(f"no unit {unit_id}")
    return u


# ------------------------------------------------------------- my layer


def set_prefs(s: Session, unit_id: int, **fields) -> UnitPrefs:
    u = _unit(s, unit_id)
    p = s.get(UnitPrefs, u.id) or UnitPrefs(unit_id=u.id)
    if "rating" in fields:
        r = fields["rating"]
        if r is not None and not (1 <= int(r) <= 5):
            raise ActionError("rating must be 1-5")
        p.rating = int(r) if r is not None else None
    if "status" in fields:
        if fields["status"] not in PREF_STATUSES:
            raise ActionError(f"status must be one of {sorted(x for x in PREF_STATUSES if x)}")
        p.status = fields["status"]
    if "notes" in fields:
        p.notes = fields["notes"] or None
    s.add(p)
    s.flush()
    return p


def add_tag(s: Session, unit_id: int, tag: str) -> list[str]:
    u = _unit(s, unit_id)
    tag = tag.strip().lower()
    if not tag:
        raise ActionError("empty tag")
    if s.get(UnitTag, (u.id, tag)) is None:
        s.add(UnitTag(unit_id=u.id, tag=tag))
        s.flush()
    return [t.tag for t in s.query(UnitTag).filter_by(unit_id=u.id).order_by(UnitTag.tag)]


def remove_tag(s: Session, unit_id: int, tag: str) -> list[str]:
    u = _unit(s, unit_id)
    s.query(UnitTag).filter_by(unit_id=u.id, tag=tag.strip().lower()).delete()
    return [t.tag for t in s.query(UnitTag).filter_by(unit_id=u.id).order_by(UnitTag.tag)]


def all_tags(s: Session) -> list[dict]:
    from sqlalchemy import func

    return [{"tag": t, "count": n} for t, n in
            s.query(UnitTag.tag, func.count()).group_by(UnitTag.tag).order_by(func.count().desc()).all()]


# --------------------------------------------------------- unit merges


def merge_units(s: Session, into_id: int, merged_id: int, reason: str | None = None) -> MergeLog:
    into, merged = _unit(s, into_id), _unit(s, merged_id)
    if into.id == merged.id:
        raise ActionError("units are already the same")
    moved_prefs = False
    p_into, p_merged = s.get(UnitPrefs, into.id), s.get(UnitPrefs, merged.id)
    prev_into = None
    if p_merged is not None:
        if p_into is None:
            s.add(UnitPrefs(unit_id=into.id, rating=p_merged.rating, status=p_merged.status, notes=p_merged.notes))
            moved_prefs = True
        else:
            prev_into = {"rating": p_into.rating, "status": p_into.status, "notes": p_into.notes}
            p_into.rating = p_into.rating or p_merged.rating
            p_into.status = p_into.status or p_merged.status
            if p_merged.notes and p_merged.notes not in (p_into.notes or ""):
                p_into.notes = f"{p_into.notes}\n\n{p_merged.notes}" if p_into.notes else p_merged.notes
    added_tags = []
    for t in s.query(UnitTag).filter_by(unit_id=merged.id).all():
        if s.get(UnitTag, (into.id, t.tag)) is None:
            s.add(UnitTag(unit_id=into.id, tag=t.tag))
            added_tags.append(t.tag)
    merged.merged_into_id = into.id
    log = MergeLog(action="merge_units", actor="user", reason=reason,
                   subject={"into_unit_id": into.id, "merged_unit_id": merged.id, "moved_prefs": moved_prefs,
                            "prev_into_prefs": prev_into, "added_tags": added_tags})
    s.add(log)
    s.flush()
    return log


def unlink_listing(s: Session, source_listing_id: int, reason: str | None = None) -> MergeLog:
    """Split one source listing off into its own unit (e.g. a wrong auto-match)."""
    sl = s.get(SourceListing, source_listing_id)
    if sl is None:
        raise ActionError("no such listing")
    old = _unit(s, sl.unit_id) if sl.unit_id else None
    if old is None:
        raise ActionError("listing is not linked")
    new = Unit(building_id=old.building_id, norm_unit=f"{old.norm_unit}~split{sl.id}", unit_label=old.unit_label)
    s.add(new)
    s.flush()
    log = MergeLog(action="unlink_listing", actor="user", reason=reason,
                   subject={"source_listing_id": sl.id, "from_unit_id": sl.unit_id, "to_unit_id": new.id,
                            "was_locked": sl.link_locked})
    sl.unit_id, sl.link_locked = new.id, True
    s.add(log)
    s.flush()
    return log


# ------------------------------------------------------ contact merges


def merge_contacts(s: Session, into_id: int, merged_id: int, reason: str | None = None) -> MergeLog:
    into, merged = s.get(Contact, into_id), s.get(Contact, merged_id)
    if into is None or merged is None or into.id == merged.id:
        raise ActionError("invalid contacts")
    moved = {"points": [], "names": [], "mentions": []}
    for p in s.query(ContactPoint).filter_by(contact_id=merged.id).all():
        p.contact_id = into.id
        moved["points"].append(p.id)
    for n in s.query(ContactName).filter_by(contact_id=merged.id).all():
        if s.query(ContactName).filter_by(contact_id=into.id, norm=n.norm).first() is None:
            n.contact_id = into.id
            moved["names"].append(n.id)
    for m in s.query(ContactMention).filter_by(contact_id=merged.id).all():
        m.contact_id = into.id
        moved["mentions"].append(m.id)
    for o in s.query(OutreachLog).filter_by(contact_id=merged.id).all():
        o.contact_id = into.id
        moved.setdefault("outreach", []).append(o.id)
    s.query(ContactMention).filter_by(suggested_contact_id=into.id, contact_id=into.id,
                                      review_state="needs_review").update({"review_state": "confirmed"})
    merged.merged_into_id = into.id
    log = MergeLog(action="merge_contacts", actor="user", reason=reason,
                   subject={"into_contact_id": into.id, "merged_contact_id": merged.id, "moved": moved})
    s.add(log)
    s.flush()
    return log


def review_mention(s: Session, mention_id: int, decision: str) -> ContactMention:
    """decision: 'merge' (into suggested contact), 'keep' (separate person)."""
    m = s.get(ContactMention, mention_id)
    if m is None:
        raise ActionError("no such mention")
    if decision == "merge":
        if not m.suggested_contact_id or not m.contact_id:
            raise ActionError("nothing to merge")
        merge_contacts(s, m.suggested_contact_id, m.contact_id, reason=f"review of mention {m.id}")
        m.review_state = "confirmed"
    elif decision == "keep":
        m.review_state = "rejected"
    else:
        raise ActionError("decision must be 'merge' or 'keep'")
    return m


# ----------------------------------------------------------------- undo


def undo(s: Session, merge_id: int) -> MergeLog:
    log = s.get(MergeLog, merge_id)
    if log is None:
        raise ActionError("no such merge")
    if log.undone_at:
        raise ActionError("already undone")
    sub = log.subject
    if log.action == "merge_units":
        merged = s.get(Unit, sub["merged_unit_id"])
        merged.merged_into_id = None
        for tag in sub.get("added_tags") or []:
            s.query(UnitTag).filter_by(unit_id=sub["into_unit_id"], tag=tag).delete()
        if sub.get("moved_prefs"):
            p = s.get(UnitPrefs, sub["into_unit_id"])
            if p is not None:
                s.delete(p)
        elif sub.get("prev_into_prefs"):
            p = s.get(UnitPrefs, sub["into_unit_id"])
            for k, v in sub["prev_into_prefs"].items():
                setattr(p, k, v)
    elif log.action in ("unlink_listing", "link_listing"):
        if sub.get("from_unit_id") is None:
            raise ActionError("this was the listing's first link; merge or split instead")
        sl = s.get(SourceListing, sub["source_listing_id"])
        sl.unit_id = sub["from_unit_id"]
        sl.link_locked = True if log.action == "link_listing" else bool(sub.get("was_locked"))
    elif log.action == "merge_contacts":
        merged = s.get(Contact, sub["merged_contact_id"])
        merged.merged_into_id = None
        mv = sub.get("moved") or {}
        for pid in mv.get("points", []):
            s.get(ContactPoint, pid).contact_id = merged.id
        for nid in mv.get("names", []):
            s.get(ContactName, nid).contact_id = merged.id
        for mid in mv.get("mentions", []):
            s.get(ContactMention, mid).contact_id = merged.id
        for oid in mv.get("outreach", []):
            s.get(OutreachLog, oid).contact_id = merged.id
    else:
        raise ActionError(f"cannot undo {log.action}")
    log.undone_at = utcnow()
    s.flush()
    return log


# -------------------------------------------------------------- outreach


def add_outreach(s: Session, contact_id: int, channel: str, direction: str = "out", summary: str | None = None,
                 outcome: str | None = None, follow_up_on: str | None = None, unit_id: int | None = None) -> OutreachLog:
    if s.get(Contact, contact_id) is None:
        raise ActionError("no such contact")
    if channel not in ("email", "call", "text", "in_person", "other"):
        raise ActionError("channel must be email/call/text/in_person/other")
    o = OutreachLog(contact_id=contact_id, channel=channel, direction=direction, summary=summary, outcome=outcome,
                    follow_up_on=date.fromisoformat(follow_up_on) if follow_up_on else None, unit_id=unit_id)
    s.add(o)
    s.flush()
    return o


def set_contact_notes(s: Session, contact_id: int, notes: str | None, name: str | None = None) -> Contact:
    c = s.get(Contact, contact_id)
    if c is None:
        raise ActionError("no such contact")
    c.notes = notes or None
    if name:
        c.display_name = name
    return c
