"""Move-in / availability date extraction from listing text.

Rules (from the brief):
  * A date stated in the description wins over the structured field.
  * Keep raw text, confidence and method alongside the parsed value.
  * Handle "now" and "flexible"; vague phrasing ("after current tenant
    leaves", "around graduation") is recorded as ``vague`` for an optional
    LLM fallback later.

Only dates anchored to availability language count ("Available June 1st",
"move-in 7/22", "starting August"), so lease-end dates like "lease through
5/24/2027" are not mistaken for move-in dates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4,
    "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9, "oct": 10, "october": 10, "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}
_MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))

ANCHOR = (
    r"(?:available|availability|avail\.?|move[- ]?in(?:\s+date)?|moving\s+in|occupancy|lease\s+(?:start|begin)s?(?:\s+on)?"
    r"|starting|start\s+date|beginning|begins|open(?:ing)?|ready)"
)
_GAP = (r"[\s:!,\-–—(]*(?:for\s+(?:an?\s+)?[a-z\s-]{0,20}?lease\s+(?:starting\s+|beginning\s+)?)?"
        r"(?:on|as\s+of|from|for|in|by|date|is|:)?[\s:,\-–—]*")

# Textual date: "June 1st, 2027", "Jun 1 2027", "June 1", "1 June 2027"
_TXT = (
    rf"(?P<qual>early|mid|mid-|late|end\s+of|beginning\s+of|the\s+end\s+of)?\s*"
    rf"(?P<mon>{_MONTH_RE})\.?\s*(?:(?P<day>\d{{1,2}})(?!\d)(?:st|nd|rd|th)?)?(?:,?\s*(?P<year>(?:20)?\d{{2}}))?\b"
)
_NUM = r"(?P<nm>\d{1,2})[/.-](?P<nd>\d{1,2})(?:[/.-](?P<ny>\d{2,4}))?"
_NOW = r"(?P<now>now|immediately|immediate(?:ly)?|today|asap|right\s+away)"
_FLEX = r"(?P<flex>flexible|negotiable|tbd|to\s+be\s+determined)"

_ANCHORED = re.compile(rf"\b{ANCHOR}{_GAP}(?:{_NOW}|{_FLEX}|{_NUM}|{_TXT})", re.I)
_VAGUE = re.compile(
    r"(after\s+(?:the\s+)?current\s+tenant|when\s+current\s+(?:tenant|lease)|around\s+graduation|end\s+of\s+(?:the\s+)?semester"
    r"|summer|fall|spring|winter)\b", re.I)


@dataclass
class AvailabilityText:
    kind: str  # date | now | flexible | vague
    date: date | None
    raw: str
    confidence: float
    method: str = "regex"


def _year_for(month: int, day: int, year: int | None, ref: date) -> tuple[int, float]:
    if year is not None:
        return (year + 2000 if year < 100 else year), 1.0
    # No year: the next occurrence on/after (ref - 30 days).
    y = ref.year
    try:
        if date(y, month, day) < ref - timedelta(days=30):
            y += 1
    except ValueError:
        pass
    return y, 0.8


def _qual_day(qual: str | None, month: int, year: int) -> tuple[int, float]:
    q = (qual or "").lower().replace("-", "").strip()
    if not q or q.startswith("beginning") or q == "early":
        return 1, (0.9 if not q else 0.6)
    if q.startswith("mid"):
        return 15, 0.6
    # late / end of
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    return (nxt - timedelta(days=1)).day, 0.6


def parse_availability(text: str | None, ref: date) -> AvailabilityText | None:
    """First availability statement in ``text``; ``ref`` resolves missing years."""
    if not text:
        return None
    for m in _ANCHORED.finditer(text):
        raw = text[m.start(): m.end()].strip()
        g = m.groupdict()
        if g.get("now"):
            return AvailabilityText("now", None, raw, 0.95)
        if g.get("flex"):
            return AvailabilityText("flexible", None, raw, 0.9)
        if g.get("nm"):
            mo, d = int(g["nm"]), int(g["nd"])
            if not (1 <= mo <= 12 and 1 <= d <= 31):
                continue
            yr, conf = _year_for(mo, d, int(g["ny"]) if g.get("ny") else None, ref)
            try:
                return AvailabilityText("date", date(yr, mo, d), raw, conf * 0.95)
            except ValueError:
                continue
        if g.get("mon"):
            mo = MONTHS[g["mon"].lower().rstrip(".")]
            yr_in = int(g["year"]) if g.get("year") else None
            if g.get("day"):
                d = int(g["day"])
                yr, conf = _year_for(mo, d, yr_in, ref)
                conf *= 0.9 if g.get("qual") else 1.0
            else:
                yr, conf = _year_for(mo, 1, yr_in, ref)
                d, qconf = _qual_day(g.get("qual"), mo, yr)
                conf = min(conf, qconf)
            try:
                return AvailabilityText("date", date(yr, mo, d), raw, round(conf, 2))
            except ValueError:
                continue
    vm = _VAGUE.search(text)
    if vm and re.search(ANCHOR, text[max(0, vm.start() - 60): vm.end()], re.I):
        lo = max(0, vm.start() - 40)
        return AvailabilityText("vague", None, text[lo: vm.end() + 20].strip(), 0.3)
    return None


@dataclass
class Effective:
    date: date | None  # None + kind=="now" means available now
    kind: str  # date | now | flexible | unknown
    source: str  # text | structured | none
    note: str | None = None


def effective_availability(structured: date | None, structured_raw: str | None,
                           text_kind: str | None, text_date: date | None, today: date,
                           text_confidence: float | None = None) -> Effective:
    """Combine structured and description-derived availability (text wins).

    Exception: a low-precision text date ("ready for October") doesn't
    override a structured date in the same month ("10/16/26").
    """
    if (text_kind == "date" and text_date and structured and (text_confidence or 0) < 0.9
            and (structured.year, structured.month) == (text_date.year, text_date.month)):
        return Effective(structured, "date", "structured", f"text says {text_date.strftime('%B %Y')}")
    if text_kind == "date" and text_date:
        note = None
        if structured and structured != text_date:
            note = f"structured field says {structured.isoformat()}"
        return Effective(text_date, "date", "text", note)
    if text_kind == "now":
        return Effective(today, "now", "text")
    if text_kind == "flexible":
        return Effective(structured, "flexible", "text")
    if structured:
        if structured <= today:
            return Effective(today, "now", "structured", f"structured date {structured.isoformat()} has passed")
        return Effective(structured, "date", "structured")
    if structured_raw and re.search(r"\bnow\b|immediate", structured_raw, re.I):
        return Effective(today, "now", "structured")
    return Effective(None, "unknown", "none")


def classify(eff: Effective, target: date, window_days: int) -> dict:
    """Relation of an effective date to the target move-in date."""
    if eff.date is None:
        return {"delta_days": None, "match": "unknown"}
    delta = (eff.date - target).days
    if eff.kind == "now":
        # Available now: only a match if the target is also within the window.
        match = "near" if abs(delta) <= window_days else "early"
    elif abs(delta) <= 7:
        match = "exact"
    elif abs(delta) <= window_days:
        match = "near"
    else:
        match = "early" if delta < 0 else "late"
    return {"delta_days": delta, "match": match}
