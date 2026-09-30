"""Contact extraction and normalization.

Sources: the structured contact block of a listing, and names/phones/emails
mentioned in the description. Only information the listing itself publishes
is recorded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

_PHONE = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?([2-9]\d{2})\)?[\s.-]?(\d{3})[\s.-]?(\d{4})(?!\d)")
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# "contact Jane Smith", "call Bob at", "email Sarah", "ask for Mike Jones"
_NAME_CUES = re.compile(
    r"\b(?i:contact|call|text|email|e-mail|ask\s+for|reach\s+out\s+to|landlord(?:\s+is)?|owner(?:\s+is)?|managed\s+by|showings?\s+(?:with|by))"
    r"\s*:?\s+([A-Z][a-z]+(?:\s+[A-Z]\.)?(?:\s+[A-Z][a-z'\-]+)?|[A-Z]\.\s*[A-Z][a-z'\-]+)"
)
_NOT_NAMES = {"Us", "Today", "Now", "For", "The", "Our", "Five", "Stone", "Please", "Me", "Us Today", "Info", "Anytime"}
# Words that mean the "name" is really a company or a verb phrase ("Owner Pays").
_NON_NAME_WORDS = {"pays", "pay", "property", "properties", "management", "real", "realty", "group", "llc", "inc",
                   "office", "team", "leasing", "rentals", "apartments", "us", "our", "today", "now", "responsible",
                   "covers", "provides", "handles", "will", "is", "at", "for"}
_COMPANY_AFTER = re.compile(r"^\s*(?:property|properties|management|realty|group|llc|inc|rentals|associates)\b", re.I)

# Per-listing relay addresses from showing services: attribute to the company, not a person.
RELAY_DOMAINS = ("tenantturnermail.com", "showmojo.com", "rently.com", "zumper.com", "tenantcloud.com")


def normalize_phone(raw: str | None) -> str | None:
    if not raw:
        return None
    m = _PHONE.search(raw)
    if not m:
        return None
    return f"+1{m.group(1)}{m.group(2)}{m.group(3)}"


def format_phone(e164: str | None) -> str | None:
    if not e164 or len(e164) != 12:
        return e164
    return f"({e164[2:5]}) {e164[5:8]}-{e164[8:]}"


def normalize_email(raw: str | None) -> str | None:
    if not raw:
        return None
    m = _EMAIL.search(raw)
    return m.group(0).lower() if m else None


def is_relay_email(email: str | None) -> bool:
    return bool(email) and email.split("@")[-1].endswith(RELAY_DOMAINS)


def normalize_name(name: str | None) -> str | None:
    if not name:
        return None
    n = re.sub(r"[^a-z\s.'-]", " ", name.lower())
    n = re.sub(r"\s+", " ", n).strip(" .")
    return n or None


def name_similarity(a: str, b: str) -> float:
    """0..1 similarity tolerant of initials: 'j. smith' ~ 'jane smith'."""
    a, b = normalize_name(a) or "", normalize_name(b) or ""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ta, tb = a.replace(".", "").split(), b.replace(".", "").split()
    if len(ta) >= 2 and len(tb) >= 2 and ta[-1] == tb[-1]:
        fa, fb = ta[0], tb[0]
        if fa == fb:
            return 0.97
        if (len(fa) == 1 and fb.startswith(fa)) or (len(fb) == 1 and fa.startswith(fb)):
            return 0.9
    return SequenceMatcher(None, a, b).ratio()


@dataclass
class Mention:
    origin: str  # contact_block | description
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    confidence: float = 1.0
    extra: dict = field(default_factory=dict)


GENERIC_NAME = re.compile(r"(?i)show\s*mojo|leasing|office|contact|management|properties|team|agent")


def extract_mentions(contact_raw: dict | None, description: str | None) -> list[Mention]:
    out: list[Mention] = []
    seen_points: set[str] = set()
    if contact_raw:
        pairs = [("name", "phone", "email"), ("secondary_name", "secondary_phone", "secondary_email")]
        for nk, pk, ek in pairs:
            name, phone, email = contact_raw.get(nk), normalize_phone(contact_raw.get(pk)), normalize_email(contact_raw.get(ek))
            if name and GENERIC_NAME.search(name):
                name = None
            if name or phone or email:
                out.append(Mention("contact_block", name, phone, email, 1.0))
                seen_points |= {p for p in (phone, email) if p}
        for role in ("managing_agent", "building_owner"):
            if contact_raw.get(role):
                out.append(Mention("contact_block", contact_raw[role], None, None, 0.9, {"role": role}))
    if description:
        phones = {normalize_phone(m.group(0)) for m in _PHONE.finditer(description)} - seen_points - {None}
        emails = {e.lower() for e in _EMAIL.findall(description)} - seen_points
        names = []
        for mm in _NAME_CUES.finditer(description):
            n = mm.group(1)
            words = n.replace(".", " ").lower().split()
            if n in _NOT_NAMES or n.split()[0] in _NOT_NAMES or any(w in _NON_NAME_WORDS for w in words):
                continue
            if _COMPANY_AFTER.match(description[mm.end():]):
                continue
            names.append(n)
        # Pair a lone cued name with a lone phone/email in the same description.
        if len(names) == 1 and len(phones) <= 1 and len(emails) <= 1 and (phones or emails):
            out.append(Mention("description", names[0], next(iter(phones), None), next(iter(emails), None), 0.7))
        else:
            for p in phones:
                out.append(Mention("description", None, p, None, 0.8))
            for e in emails:
                out.append(Mention("description", None, None, e, 0.8))
            for n in names:
                out.append(Mention("description", n, None, None, 0.5))
    return out
