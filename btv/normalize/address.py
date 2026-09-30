"""Address normalization for building/unit matching.

"46 Lafountain St Apt 1" and "46 Lafountain Street #1" both become building
key ``46 lafountain st|burlington|vt`` with unit ``1``. The goal is stable,
conservative keys: when unsure, keep more of the original rather than
over-merging (merges are cheap to do by hand, silent wrong merges are not).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SUFFIXES = {
    "street": "st", "st": "st", "str": "st",
    "avenue": "ave", "ave": "ave", "av": "ave",
    "road": "rd", "rd": "rd",
    "drive": "dr", "dr": "dr",
    "lane": "ln", "ln": "ln",
    "place": "pl", "pl": "pl",
    "court": "ct", "ct": "ct",
    "terrace": "ter", "ter": "ter", "terr": "ter",
    "boulevard": "blvd", "blvd": "blvd",
    "circle": "cir", "cir": "cir",
    "parkway": "pkwy", "pkwy": "pkwy",
    "highway": "hwy", "hwy": "hwy",
    "square": "sq", "sq": "sq",
    "way": "way", "trace": "trce", "trce": "trce",
    "extension": "ext", "ext": "ext",
}
DIRECTIONS = {"north": "n", "south": "s", "east": "e", "west": "w", "n": "n", "s": "s", "e": "e", "w": "w"}
STATES = {"vermont": "vt", "vt": "vt", "new york": "ny", "ny": "ny", "new hampshire": "nh", "nh": "nh",
          "ca": "ca", "california": "ca", "ma": "ma", "me": "me"}
KNOWN_CITIES = {
    "burlington", "south burlington", "winooski", "essex", "essex junction", "colchester", "shelburne",
    "williston", "montpelier", "barre", "northfield", "milton", "hinesburg", "charlotte", "richmond",
    "jericho", "underhill", "st albans", "saint albans", "vergennes", "middlebury", "waterbury",
    "stowe", "bakersfield",
}

UNIT_WORDS = r"(?:(?:apt|apartment|unit|ste|suite|no|rm|room|fl|floor)\b\.?|#)"
_UNIT_RE = re.compile(rf"(?:^|[\s,])(?:{UNIT_WORDS})\s*#?\s*([a-z0-9][a-z0-9/-]*)", re.I)


@dataclass
class NormalizedAddress:
    street_number: str | None
    street: str | None
    city: str | None
    state: str | None
    zip: str | None
    unit: str  # "" when none
    display: str

    @property
    def building_key(self) -> str:
        return "|".join([f"{self.street_number or ''} {self.street or ''}".strip(), self.city or "", self.state or ""])


def norm_unit(unit: str | None) -> str:
    if not unit:
        return ""
    u = unit.lower().strip()
    u = re.sub(rf"^{UNIT_WORDS}\s*", "", u)
    u = u.replace("#", "").replace(" ", "").strip(".-,")
    u = re.sub(r"^0+(?=\w)", "", u)
    return u


def _norm_street(street: str) -> str:
    s = street.lower().replace(".", " ").replace(",", " ")
    s = re.sub(r"\bsaint\b", "st", s)
    words = s.split()
    out = []
    for i, w in enumerate(words):
        if w in DIRECTIONS and (i == 0 or i == len(words) - 1) and len(words) > 1:
            out.append(DIRECTIONS[w])
        elif w in SUFFIXES and i == len(words) - 1 and i > 0:
            out.append(SUFFIXES[w])
        elif w in SUFFIXES and i > 0 and i == len(words) - 2 and words[-1] in DIRECTIONS:
            out.append(SUFFIXES[w])
        else:
            out.append(w)
    return " ".join(out)


def normalize_address(raw: str | None, unit_raw: str | None = None, default_city: str | None = None,
                      default_state: str = "vt") -> NormalizedAddress | None:
    if not raw or not raw.strip():
        return None
    text = re.sub(r"\s+", " ", raw.replace(" ", " ")).strip()
    # Multi-address buildings ("295 Pearl St / 10 Hungerford Terrace"): use the first.
    text = re.sub(r"\s+/\s+\d[^,]*", "", text)

    zip_code = None
    m = re.search(r"\b(\d{5})(?:-\d{4})?\s*$", text)
    if m:
        zip_code = m.group(1)
        text = text[: m.start()].strip(" ,")

    parts = [p.strip() for p in text.split(",") if p.strip()]
    state = None
    if parts:
        last = parts[-1].lower()
        m = re.match(r"^(.*?)\s*\b(vt|vermont|ny|nh|ca|ma|me)$", last)
        if last in STATES:
            state = STATES[last]
            parts = parts[:-1]
        elif m and m.group(1):
            state = STATES.get(m.group(2))
            parts[-1] = m.group(1).strip()
    city = None
    if len(parts) >= 2 and not re.search(r"\d", parts[-1]) and not _UNIT_RE.search(" " + parts[-1]):
        city = parts[-1].lower()
        parts = parts[:-1]
    elif len(parts) == 1:
        low = parts[0].lower()
        for c in sorted(KNOWN_CITIES, key=len, reverse=True):
            if low.endswith(" " + c):
                city = c
                parts[0] = parts[0][: -len(c)].strip()
                break
    if city:
        city = re.sub(r"^saint\b", "st", city)

    street_part = parts[0] if parts else ""
    extra_parts = parts[1:]

    unit = unit_raw
    # Unit embedded in the street part: "... Apt #3", "... - 3", "... #1A"
    m = _UNIT_RE.search(street_part)
    if m:
        unit = unit or m.group(1)
        street_part = street_part[: m.start()].strip(" ,-")
    m = re.search(r"\s+-\s+([a-z0-9][a-z0-9 /-]{0,7})$", street_part, re.I)
    if m:
        unit = unit or m.group(1)
        street_part = street_part[: m.start()].strip()
    # Unit as its own comma part: "25 Elmwood Ave, 17" / "..., Unit 2" / "..., Apt. #3"
    for p in extra_parts:
        mm = _UNIT_RE.search(" " + p)
        if mm:
            unit = unit or mm.group(1)
        elif re.fullmatch(r"#?\s*[a-z0-9]{1,5}", p, re.I):
            unit = unit or p

    m = re.match(r"^(\d+(?:\s*[-–]\s*\d+)?(?:\s+1/2)?[a-z]?)\s+(.*)$", street_part, re.I)
    if m:
        number = re.sub(r"\s+", "", m.group(1)).replace("–", "-").lower().replace("1/2", " 1/2")
        street = _norm_street(m.group(2))
    else:
        number, street = None, _norm_street(street_part) if street_part else None

    city = city or (default_city.lower() if default_city else None)
    return NormalizedAddress(
        street_number=number,
        street=street or None,
        city=city,
        state=state or default_state,
        zip=zip_code,
        unit=norm_unit(unit),
        display=_display(number, street_part, m, city, state or default_state, zip_code),
    )


def _display(number, street_part, m, city, state, zip_code) -> str:
    street_disp = street_part if street_part else ""
    tail = ", ".join(p for p in (city.title() if city else None, " ".join(x for x in ((state or "").upper(), zip_code or "") if x)) if p)
    return ", ".join(p for p in (street_disp.strip(), tail) if p)
