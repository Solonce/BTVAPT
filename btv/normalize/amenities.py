"""Surface amenities mentioned in listing text (never inferred as fact).

Returns canonical tags with the phrase that triggered each, so the UI can
show "W/D (\"washer and dryer in unit\")". User tags stay manual.
"""

from __future__ import annotations

import re

PATTERNS: list[tuple[str, str]] = [
    ("washer/dryer in unit", r"\b(?:in[- ]unit\s+(?:laundry|washer)|washer\s*(?:&|and|/)\s*dryer\s+(?:in|inside)\s+(?:the\s+)?(?:unit|apartment)|w/d\s+in\s+unit|washer\s+and\s+dryer\s+included|private\s+laundry)\b"),
    ("washer/dryer", r"\b(?:washer\s*(?:&|and|/)\s*dryer|w/d|washer|dryer)\b"),
    ("laundry on site", r"\b(?:(?:shared|coin[- ]op|on[- ]site|common|basement)\s+laundry|laundry\s+(?:room|on[- ]site|facilit\w+|in\s+(?:the\s+)?(?:building|basement)))\b"),
    ("balcony", r"\bbalcon(?:y|ies)\b"),
    ("porch/deck", r"\b(?:porch|deck|patio|veranda)\b"),
    ("roof access", r"\b(?:roof\s*(?:top)?\s*(?:access|deck|terrace|patio)|rooftop)\b"),
    ("yard", r"\b(?:back\s*yard|front\s*yard|yard|garden)\b"),
    ("off-street parking", r"\b(?:off[- ]street\s+parking|parking\s+(?:space|spot|included|available)|driveway|\d+\s+parking)\b"),
    ("garage", r"\bgarage\b"),
    ("dishwasher", r"\bdishwasher\b"),
    ("air conditioning", r"\b(?:air\s+condition\w*|a/c|central\s+air|mini[- ]?split|heat\s+pump)\b"),
    ("hardwood floors", r"\bhardwood\b"),
    ("fireplace", r"\b(?:fireplace|wood\s*stove)\b"),
    ("storage", r"\b(?:storage|walk[- ]in\s+closet)\b"),
    ("heat included", r"\bheat(?:\s+and\s+hot\s+water)?\s+(?:is\s+)?included\b"),
    ("pets allowed", r"\b(?:pet[- ]friendly|pets?\s+(?:allowed|welcome|ok)|cats?\s+(?:allowed|ok|welcome)|dogs?\s+(?:allowed|ok|welcome))\b"),
    ("no pets", r"\b(?:no\s+pets|pets?\s+not\s+allowed|(?:cats|dogs)\s+(?:are\s+)?not\s+allowed)\b"),
    ("furnished", r"\bfurnished\b"),
    ("character", r"\b(?:victorian|historic|charming|character|exposed\s+brick|high\s+ceilings|original\s+(?:woodwork|trim))\b"),
    ("lake view", r"\blake\s+views?\b"),
    ("smoking allowed", r"\bsmoking\s+(?:allowed|permitted|ok)\b"),
]
_COMPILED = [(tag, re.compile(p, re.I)) for tag, p in PATTERNS]


def detect_amenities(*texts: str | None) -> list[dict]:
    blob = "\n".join(t for t in texts if t)
    found: dict[str, str] = {}
    for tag, rx in _COMPILED:
        m = rx.search(blob)
        if m and tag not in found:
            found[tag] = m.group(0)
    if "washer/dryer in unit" in found:
        found.pop("washer/dryer", None)
    if "no pets" in found:
        found.pop("pets allowed", None)
    return [{"tag": t, "evidence": e} for t, e in found.items()]
