"""Pull listing fields out of free text (a pasted Facebook post, an email).

Dates and contacts are handled by the normal pipeline; this extracts rent,
beds/baths, an address if one is written out, and pet hints.
"""

from __future__ import annotations

import re

from btv.normalize.address import SUFFIXES

_SUFFIX_RE = "|".join(sorted({k for k in SUFFIXES} | {"st", "ave", "rd"}, key=len, reverse=True))
_ADDRESS = re.compile(
    rf"\b(\d{{1,5}}(?:-\d{{1,5}})?(?:\s+1/2)?\s+(?:[NSEW]\.?\s+|North\s+|South\s+|East\s+|West\s+)?"
    rf"(?:[A-Z][\w'.-]*\s+){{1,3}}(?:{_SUFFIX_RE})\b\.?(?:\s*(?:#|apt\.?|unit)\s*[\w-]+)?"
    rf"(?:,?\s+(?:Burlington|South Burlington|Winooski|Essex(?: Junction)?|Colchester|Williston|Shelburne))?)",
    re.I,
)
_RENT = re.compile(r"\$\s?(\d{1,2}(?:,\d{3})|\d{3,4})(?:\.\d{2})?\s*(?:/\s*(?:mo|month)|per\s+month|a\s+month|monthly)?", re.I)
_RENT_WORD = re.compile(r"(?i)\b(?:rent|asking|price)\s*(?:is|:)?\s*\$?\s?(\d{1,2},\d{3}|\d{3,4})\b")
_BEDS = re.compile(r"(?i)\b(\d(?:\.5)?|one|two|three|four|five)\s*(?:-|\s)?(?:br\b|bd\b|bed(?:room)?s?\b)")
_BATHS = re.compile(r"(?i)\b(\d(?:\.5)?)\s*(?:-|\s)?(?:ba\b|bath(?:room)?s?\b)")
_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}


def parse_freeform(text: str) -> dict:
    t = text or ""
    rent = None
    for rx in (_RENT_WORD, _RENT):
        m = rx.search(t)
        if m:
            v = int(m.group(1).replace(",", ""))
            if 300 <= v <= 15000:
                rent = v
                break
    beds = None
    if re.search(r"(?i)\bstudio\b", t):
        beds = 0.0
    m = _BEDS.search(t)
    if m:
        g = m.group(1).lower()
        beds = float(_WORDS.get(g, g))
    m = _BATHS.search(t)
    baths = float(m.group(1)) if m else None
    m = _ADDRESS.search(t)
    address = re.sub(r"\s+", " ", m.group(1)).strip(" ,.") if m else None
    pets = None
    if re.search(r"(?i)\bno\s+pets\b|pets?\s+not\s+allowed", t):
        pets = "no pets"
    elif re.search(r"(?i)pet[- ]friendly|pets?\s+(?:ok|allowed|welcome)|cats?\s+ok|dogs?\s+ok", t):
        pets = "pets ok"
    title = next((ln.strip() for ln in t.splitlines() if ln.strip()), "")[:120] or None
    return {"rent": rent, "beds": beds, "baths": baths, "address": address, "pets": pets, "title": title}
