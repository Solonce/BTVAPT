"""Scam risk signals for listings, especially Craigslist / social posts.

Every point comes with a human-readable reason and evidence, so the UI can
say *why* something looks off. It is a heuristic to prompt caution, never a
verdict.

Strongest real-world signal around Burlington: a scammer copies a property
manager's listing (text and/or photos), lists it cheaper on Craigslist or
Facebook, and asks for a deposit before any showing. We catch that by
comparing untrusted listings against manager-site listings.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field

TRUSTED_PLATFORMS = {"buildium", "nesthub", "appfolio", "rentcafe"}

TEXT_FLAGS: list[tuple[int, str, re.Pattern]] = [
    (3, "Asks for payment by wire, gift card, crypto or similar",
     re.compile(r"(?i)\b(wire\s+transfer|western\s+union|moneygram|gift\s*cards?|bitcoin|crypto|cashier'?s\s+check|money\s+order)\b")),
    (2, "Wants money via Zelle/Venmo/CashApp before a showing",
     re.compile(r"(?i)\b(zelle|venmo|cash\s*app|paypal)\b.{0,80}\b(deposit|hold|reserve|first)\b|\b(deposit|hold|reserve)\b.{0,80}\b(zelle|venmo|cash\s*app|paypal)\b")),
    (3, "Owner says they're away (abroad, deployed, missionary work)",
     re.compile(r"(?i)\b(out\s+of\s+the\s+(?:country|state)|overseas|missionary|mission\s+trip|deployed|military\s+(?:assignment|duty)|relocated\s+(?:to|for)\s+(?:work|a\s+job)\s+(?:in|out))")),
    (3, "Can't show the unit / keys will be mailed",
     re.compile(r"(?i)\b(can(?:'|no)?t\s+show|cannot\s+show|no\s+showings?|keys?\s+(?:will\s+be\s+)?(?:mailed|sent|shipped)|drive\s+by\s+(?:and|to)\s+(?:see|look)|view\s+(?:it\s+)?from\s+(?:the\s+)?outside)")),
    (2, "Deposit requested to hold the unit",
     re.compile(r"(?i)\b(send|pay|wire)\s+(?:the\s+|a\s+)?(?:deposit|first\s+month)|\bdeposit\s+to\s+(?:hold|secure|reserve)|\bhold\s+(?:it|the\s+(?:unit|apartment|place))\s+(?:with|for)\s+(?:a\s+)?deposit")),
    (1, "Moves conversation to personal email/text right away",
     re.compile(r"(?i)\b(email|text)\s+me\s+(?:directly\s+)?(?:at|on)\b|\bcontact\s+me\s+via\s+email\s+only\b")),
    (1, "Pressure language",
     re.compile(r"(?i)\b(first\s+come[, ]+first\s+serve|won'?t\s+last|act\s+fast|many\s+people\s+are\s+interested|serious\s+inquiries\s+only)\b")),
]


@dataclass
class Risk:
    score: int = 0
    reasons: list[dict] = field(default_factory=list)
    verified_source: bool = False

    def add(self, points: int, reason: str, evidence: str | None = None, kind: str = "text") -> None:
        self.score += points
        self.reasons.append({"points": points, "reason": reason, "evidence": evidence, "kind": kind})

    @property
    def level(self) -> str:
        if self.verified_source and self.score < 3:
            return "verified"
        if self.score >= 5:
            return "high"
        if self.score >= 3:
            return "medium"
        if self.score > 0:
            return "low"
        return "none"

    def as_dict(self) -> dict:
        return {"level": self.level, "score": self.score, "reasons": self.reasons,
                "verified_source": self.verified_source}


def text_flags(text: str | None) -> list[tuple[int, str, str]]:
    out = []
    for pts, reason, rx in TEXT_FLAGS:
        m = rx.search(text or "")
        if m:
            out.append((pts, reason, m.group(0)))
    return out


def shingles(text: str | None, k: int = 6) -> set[int]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {hash(" ".join(words[i:i + k])) for i in range(max(0, len(words) - k + 1))}


class ScamIndex:
    """Built once per request from all unit summaries + their descriptions."""

    def __init__(self, units: list[dict], descriptions: dict[int, str | None], photos: dict[int, list[str]]):
        self.units = {u["id"]: u for u in units}
        self.medians: dict[float, float] = {}
        by_beds: dict[float, list[int]] = defaultdict(list)
        for u in units:
            if u["rent"] and u["beds"] is not None and u["status"] != "gone":
                by_beds[float(u["beds"])].append(u["rent"])
        for b, rents in by_beds.items():
            if len(rents) >= 5:
                self.medians[b] = statistics.median(rents)
        self.trusted_shingles: dict[int, set[int]] = {}
        self.inverted: dict[int, list[int]] = defaultdict(list)
        self.photo_owner: dict[str, int] = {}
        for uid, u in self.units.items():
            if not _trusted(u):
                continue
            sh = shingles(descriptions.get(uid))
            if len(sh) >= 8:
                self.trusted_shingles[uid] = sh
                for h in sh:
                    self.inverted[h].append(uid)
            for p in photos.get(uid) or []:
                self.photo_owner.setdefault(_photo_key(p), uid)

    def assess(self, unit: dict, description: str | None, photos: list[str]) -> dict:
        r = Risk(verified_source=_trusted(unit))
        for pts, reason, ev in text_flags(f"{unit.get('title') or ''}\n{description or ''}"):
            r.add(pts, reason, ev)
        med = self.medians.get(float(unit["beds"])) if unit.get("beds") is not None else None
        if med and unit.get("rent") and not r.verified_source:
            ratio = unit["rent"] / med
            label = "studio" if unit["beds"] == 0 else f"{unit['beds']:g}-bed"
            if ratio < 0.6:
                r.add(3, f"Rent is {round((1 - ratio) * 100)}% below the typical {label} here",
                      f"${unit['rent']:,} vs median ${med:,.0f}", "price")
            elif ratio < 0.75:
                r.add(1, f"Rent is well below the typical {label} here",
                      f"${unit['rent']:,} vs median ${med:,.0f}", "price")
        if not r.verified_source:
            sh = shingles(description)
            if len(sh) >= 8:
                hits = Counter(uid for h in sh for uid in self.inverted.get(h, ()) if uid != unit["id"])
                for uid, n in hits.most_common(1):
                    other = self.trusted_shingles[uid]
                    jac = n / len(sh | other)
                    if jac >= 0.35:
                        o = self.units[uid]
                        r.add(4, "Text copied from a property manager's listing",
                              f"{round(jac * 100)}% match with {o['sources'][0]['source']} at {o['address']}", "copy")
            for p in photos:
                uid = self.photo_owner.get(_photo_key(p))
                if uid and uid != unit["id"]:
                    o = self.units[uid]
                    r.add(4, "Uses a photo from a property manager's listing",
                          f"same photo as {o['sources'][0]['source']} at {o['address']}", "copy")
                    break
            if not unit.get("address"):
                r.add(1, "No street address given", None, "address")
        return r.as_dict()


def _trusted(u: dict) -> bool:
    return any(s.get("platform") in TRUSTED_PLATFORMS for s in u.get("sources") or [])


def _photo_key(url: str) -> str:
    # Same image served in different sizes should match.
    return re.sub(r"(_\d+x\d+|/(?:large|medium|small)\.(?:jpe?g|png))", "", url.split("?")[0])
