"""Buildium public listings (``{company}.managebuilding.com``).

Buildium's public pages are DotVVM apps: the full page state, including every
listing, is serialized as JSON in the ``__dot_viewmodel_root`` hidden input,
so no browser is needed.

* ``/Resident/PublicPages/ApartmentSearch.aspx`` holds all active listings
  (address parts, rent, beds/baths, structured availability date, full unit
  description, contact block). No pagination.
* ``apartmentdetail.aspx`` adds the rental/building feature lists (amenities).
* ``apartmentImages.aspx`` lists every photo.

Detail and image pages are only refetched when a listing is new, its
search-page record changed, or ``detail_refresh_hours`` (default 24) passed.

Some companies disable Buildium's public listings (the page redirects to the
resident login); the adapter raises so the run fails loudly instead of
reporting zero listings.
"""

from __future__ import annotations

import html
import json
import re
from datetime import date, datetime
from typing import Iterator
from urllib.parse import urlencode

from btv.sources.base import Adapter, ParsedListing, html_to_text, register, to_number

SEARCH_PATH = "/Resident/PublicPages/ApartmentSearch.aspx"
DETAIL_PATH = "/Resident/PublicPages/apartmentdetail.aspx"
IMAGES_PATH = "/Resident/PublicPages/apartmentImages.aspx"

_VM_RE = re.compile(r"""id=["']?__dot_viewmodel_root["']?\s+value=(?:'([^']*)'|"([^"]*)")""")


class BuildiumError(Exception):
    pass


def extract_viewmodel(page: str) -> dict:
    m = _VM_RE.search(page)
    if not m:
        raise BuildiumError("no DotVVM viewmodel found (layout changed or login redirect)")
    return json.loads(html.unescape(m.group(1) or m.group(2)))["viewModel"]


def _kv(items: list[dict]) -> dict[str, str]:
    return {x["Key"]: x["Value"] for x in items}


def parse_search_page(page: str) -> list[dict[str, str]]:
    vm = extract_viewmodel(page)
    content = vm.get("ContentViewModel") or {}
    if "Listings" not in content:
        raise BuildiumError("search page has no Listings (public listings disabled?)")
    return [_kv(item) for item in content["Listings"] or []]


def parse_detail_page(page: str) -> dict:
    vm = extract_viewmodel(page)
    fields = _kv(vm.get("HtmlContainer") or [])
    amenities: list[str] = []
    for key in ("rentalFeaturesList", "BuildingFeaturesList"):
        amenities += [html_to_text(li) for li in re.findall(r"<li[^>]*>(.*?)</li>", fields.get(key) or "", re.S)]
    return {
        "amenities": [a for a in amenities if a],
        "lease_terms": html_to_text(fields.get("lblLeaseTerms") or fields.get("leaseTerms")),
        "building_description": html_to_text(fields.get("lblBuildingDescription")),
        "available_label": fields.get("lblAvailableDate"),
        "deposit_label": fields.get("lblSecurityDeposit"),
    }


def parse_images_page(page: str) -> list[str]:
    vm = extract_viewmodel(page)
    urls = []
    for img in vm.get("Images") or []:
        url = img.get("VideoUrl") if img.get("IsVideo") else img.get("LargeThumbUrl")
        if url:
            urls.append(url)
    return urls


def parse_buildium_date(value: str | None) -> date | None:
    """'Sunday, June 1, 2025' -> date. Buildium uses 12/31/9999 for 'none'."""
    if not value:
        return None
    for fmt in ("%A, %B %d, %Y", "%m/%d/%Y", "%B %d, %Y"):
        try:
            d = datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
        return None if d.year >= 9999 else d
    return None


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


# Search-page keys that don't affect listing content (volatile or presentational).
_VOLATILE_KEYS = {"imageUrl", "applicationLink", "mapLink", "listingid1", "listingid2"}


def listing_summary(rec: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in rec.items() if k not in _VOLATILE_KEYS}


def to_parsed(rec: dict[str, str], base_url: str, detail: dict | None, photos: list[str] | None) -> ParsedListing:
    qs = urlencode({"listingId": rec["listingid"], "unitid": rec.get("unitid", ""), "buildingid": rec.get("buildingid", "")})
    url = f"{base_url}{DETAIL_PATH}?{qs}"
    address = ", ".join(p for p in (_clean(rec.get("line1")), _clean(rec.get("line2")), _clean(rec.get("line3"))) if p)
    city_line = " ".join(p for p in (_clean(rec.get("state")), _clean(rec.get("postalcode"))) if p)
    address_raw = ", ".join(p for p in (address or _clean(rec.get("addresslines")), _clean(rec.get("city")), city_line) if p)
    description = html_to_text(rec.get("unitdescription"))
    prop_desc = html_to_text(rec.get("propertydescription"))
    if prop_desc and prop_desc not in (description or ""):
        description = f"{description}\n\n{prop_desc}" if description else prop_desc
    detail = detail or {}
    contact = {
        "name": _clean(rec.get("contactdescription")),
        "phone": _clean(rec.get("contactphone")),
        "email": _clean(rec.get("contactemail")),
        "website": _clean(rec.get("contactwebsite")),
    }
    return ParsedListing(
        external_id=rec["listingid"],
        url=url,
        title=_clean(rec.get("buildingname")) or _clean(rec.get("propertyname")),
        address_raw=address_raw or None,
        unit_raw=_clean(rec.get("unitnumber")),
        rent=to_number(rec.get("listingrent"), int),
        beds=_beds(rec),
        baths=_baths(rec.get("bathtypedesc")),
        sqft=to_number(rec.get("squarefootage"), int),
        avail_structured_raw=_clean(rec.get("availabilitydate")),
        avail_structured_date=parse_buildium_date(rec.get("availabilitydate")),
        description=description,
        amenities=detail.get("amenities") or [],
        photos=photos or [],
        contact_raw={k: v for k, v in contact.items() if v} or None,
        extra={
            "platform": "buildium",
            "buildium": listing_summary(rec),
            "lease_terms": detail.get("lease_terms") or _clean(rec.get("leaseterms")),
            "deposit": to_number(rec.get("securitydeposit"), int),
            "year_built": to_number(rec.get("yearbuilt"), int),
            "property_name": _clean(rec.get("propertyname")),
            "list_date": str(parse_buildium_date(rec.get("listdate")) or ""),
        },
    )


def _beds(rec: dict[str, str]) -> float | None:
    desc = (rec.get("bedtypedesc") or "").lower()
    if "studio" in desc:
        return 0.0
    m = re.search(r"(\d+(?:\.\d+)?)", desc)
    return float(m.group(1)) if m else None


def _baths(desc: str | None) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)", desc or "")
    return float(m.group(1)) if m else None


@register
class BuildiumAdapter(Adapter):
    platform = "buildium"
    parser_version = "buildium-1"

    def run(self, ctx) -> Iterator[ParsedListing]:
        base = self.config["base_url"].rstrip("/")
        want_details = bool(self.config.get("fetch_details", True))
        ctx.step("fetching search page")
        res, fetch_id = self.fetcher.get(base + SEARCH_PATH)
        if "/portal/login" in res.url or "/apps/portal" in res.url:
            raise BuildiumError("public listings are disabled (redirected to resident login)")
        records = parse_search_page(res.text)
        ctx.set_total(len(records) + 1)
        ctx.advance(step=f"found {len(records)} listings")
        for rec in records:
            ext = rec["listingid"]
            detail, photos, reused = None, None, False
            if want_details:
                qs = {"listingId": ext, "unitid": rec.get("unitid", ""), "buildingid": rec.get("buildingid", "")}
                detail_url = f"{base}{DETAIL_PATH}?{urlencode(qs)}"
                prev = self.previous_snapshot(ext)
                prev_summary = (prev.extra or {}).get("buildium") if prev else None
                if prev and self.detail_is_fresh(detail_url, prev_summary, listing_summary(rec)):
                    detail = {"amenities": prev.amenities, "lease_terms": (prev.extra or {}).get("lease_terms")}
                    photos, reused = prev.photos, True
                else:
                    ctx.step(f"detail {rec.get('addresslines', ext)}")
                    d_res, _ = self.fetcher.get(detail_url)
                    detail = parse_detail_page(d_res.text)
                    img_qs = {"listingId": ext, "unitId": rec.get("unitid", ""), "buildingId": rec.get("buildingid", "")}
                    i_res, _ = self.fetcher.get(f"{base}{IMAGES_PATH}?{urlencode(img_qs)}")
                    photos = parse_images_page(i_res.text)
            pl = to_parsed(rec, base, detail, photos)
            pl.raw_fetch_id = fetch_id
            yield pl
            ctx.advance(step=f"{'cached' if reused else 'parsed'} {pl.address_raw or ext}")
