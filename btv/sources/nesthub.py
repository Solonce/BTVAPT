"""Nesthub property-management websites (e.g. burlingtonproperty.management).

Nesthub sites expose a JSON API used by their own listing widget:

* ``GET /_system/api/listings?limit=1000`` returns every active listing with
  address, lat/lon, rent, beds/baths, size, availability date, description,
  features, utilities, pet flags and contact info; paginated.
* ``GET /_system/api/listings/{id}`` adds the full photo list.

Photo lists are refetched only when a listing is new, changed, or older than
``detail_refresh_hours``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Iterator

from btv.sources.base import Adapter, ParsedListing, html_to_text, parse_date, register, to_number

LIST_PATH = "/_system/api/listings"

_UTILITIES = [
    ("isWaterIncluded", "water"),
    ("isSewageIncluded", "sewer"),
    ("isGarbageIncluded", "trash"),
    ("isElectricIncluded", "electric"),
    ("isGasIncluded", "gas"),
    ("isInternetIncluded", "internet"),
    ("isCableIncluded", "cable"),
    ("isSnowRemovalIncluded", "snow removal"),
    ("isLandscapingIncluded", "landscaping"),
]

# Keys that change without the listing changing.
_VOLATILE_KEYS = {"advertisedDate", "isFeatured", "listingImageID", "imageUrl"}


def _truthy(v) -> bool:
    return str(v).lower() in ("1", "true", "yes")


def _clean(v) -> str | None:
    if v in (None, False):
        return None
    v = str(v).strip()
    return v or None


def listing_summary(rec: dict) -> dict:
    return {k: v for k, v in rec.items() if k not in _VOLATILE_KEYS}


def split_unit(address: str | None) -> tuple[str | None, str | None]:
    """'133 Elmwood Avenue - 4' -> ('133 Elmwood Avenue', '4')."""
    if not address:
        return None, None
    street, sep, unit = address.rpartition(" - ")
    if sep and unit.strip() and len(unit.strip()) <= 8:
        return street.strip(), unit.strip()
    return address.strip(), None


def to_parsed(rec: dict, base_url: str, photos: list[str] | None) -> ParsedListing:
    street, unit = split_unit(rec.get("address"))
    city_line = " ".join(p for p in (_clean(rec.get("stateID")), _clean(rec.get("postalCode"))) if p)
    address_raw = ", ".join(
        p for p in (street, _clean(rec.get("address2")), _clean(rec.get("city")), city_line) if p
    )
    try:
        features = json.loads(rec.get("features") or "[]")
    except (TypeError, ValueError):
        features = []
    utilities = [name for key, name in _UTILITIES if _truthy(rec.get(key))]
    pets_bits = []
    if rec.get("acceptCats") is not None:
        pets_bits.append("cats ok" if _truthy(rec["acceptCats"]) else "no cats")
    if rec.get("acceptDogs") is not None:
        pets_bits.append("dogs ok" if _truthy(rec["acceptDogs"]) else "no dogs")
    if _clean(rec.get("petDescription")):
        pets_bits.append(html_to_text(rec["petDescription"]))
    parking_bits = [p for p in (_clean(rec.get("parkingTypeName")),) if p]
    if to_number(rec.get("parkingSpaces"), int):
        parking_bits.append(f"{to_number(rec.get('parkingSpaces'), int)} spaces")
    contact = {
        "name": _clean(rec.get("contactName")),
        "phone": _clean(rec.get("phone")),
        "email": _clean(rec.get("email")),
        "secondary_name": _clean(rec.get("secondaryContactName")),
        "secondary_phone": _clean(rec.get("secondaryPhone")),
        "secondary_email": _clean(rec.get("secondaryEmail")),
        "managing_agent": _clean(rec.get("managingAgent")),
        "building_owner": _clean(rec.get("buildingOwner")),
    }
    page = rec.get("pageUrl") or f"/_system/listings/{rec['listingID']}/{rec.get('url', '')}"
    advertised = to_number(rec.get("advertisedDate"), int)
    return ParsedListing(
        external_id=str(rec["listingID"]),
        url=base_url + page,
        title=_clean(rec.get("headline")),
        address_raw=address_raw or None,
        unit_raw=unit,
        rent=to_number(rec.get("rent"), int),
        beds=to_number(rec.get("beds")),
        baths=to_number(rec.get("baths")),
        sqft=to_number(rec.get("size"), int),
        avail_structured_raw=_clean(rec.get("dateAvailable")),
        avail_structured_date=parse_date(rec.get("dateAvailable")),
        description=html_to_text(rec.get("description")),
        pets="; ".join(pets_bits) or None,
        parking="; ".join(parking_bits) or None,
        utilities=", ".join(utilities) if utilities else ("none included" if _truthy(rec.get("noUtilitiesIncluded")) else None),
        amenities=[f for f in features if isinstance(f, str)],
        photos=photos or [],
        contact_raw={k: v for k, v in contact.items() if v} or None,
        extra={
            "platform": "nesthub",
            "nesthub": listing_summary(rec),
            "lat": to_number(rec.get("latitude")),
            "lon": to_number(rec.get("longitude")),
            "deposit": to_number(rec.get("deposit"), int),
            "furnished": _truthy(rec.get("isFurnished")) if rec.get("isFurnished") is not None else None,
            "lease_terms": html_to_text(rec.get("leaseDescription")),
            "feed": _clean(rec.get("feedTypeName")),
            "feed_source_id": _clean(rec.get("feedSourceID")),
            "advertised_at": datetime.fromtimestamp(advertised, timezone.utc).isoformat() if advertised else None,
        },
    )


@register
class NesthubAdapter(Adapter):
    platform = "nesthub"
    parser_version = "nesthub-1"

    def run(self, ctx) -> Iterator[ParsedListing]:
        base = self.config["base_url"].rstrip("/")
        want_photos = bool(self.config.get("fetch_details", True))
        records: list[tuple[dict, int]] = []
        page = 1
        ctx.step("fetching listings")
        while True:
            res, fetch_id = self.fetcher.get(f"{base}{LIST_PATH}?limit=1000&page={page}",
                                             headers={"Accept": "application/json"})
            data = res.json()
            if "listings" not in data:
                raise ValueError("unexpected Nesthub response (no 'listings' key)")
            records += [(r, fetch_id) for r in data["listings"]]
            pag = data.get("pagination") or {}
            if page >= int(pag.get("total_pages") or 1):
                break
            page += 1
        ctx.set_total(len(records) + 1)
        ctx.advance(step=f"found {len(records)} listings")
        for rec, fetch_id in records:
            if not _truthy(rec.get("isActive", "1")):
                ctx.advance()
                continue
            ext = str(rec["listingID"])
            photos, reused = None, False
            if want_photos:
                detail_url = f"{base}{LIST_PATH}/{ext}"
                prev = self.previous_snapshot(ext)
                prev_summary = (prev.extra or {}).get("nesthub") if prev else None
                if prev and self.detail_is_fresh(detail_url, prev_summary, listing_summary(rec)):
                    photos, reused = prev.photos, True
                else:
                    ctx.step(f"photos {rec.get('address', ext)}")
                    d_res, _ = self.fetcher.get(detail_url, headers={"Accept": "application/json"})
                    photos = [base + img["url"] if img["url"].startswith("/") else img["url"]
                              for img in d_res.json().get("images") or [] if img.get("url")]
            pl = to_parsed(rec, base, photos)
            pl.raw_fetch_id = fetch_id
            yield pl
            ctx.advance(step=f"{'cached' if reused else 'parsed'} {pl.address_raw or ext}")
