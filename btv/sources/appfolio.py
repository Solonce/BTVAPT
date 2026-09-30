"""AppFolio public listings (``{company}.appfolio.com/listings``).

The listings page is server-rendered: one ``.js-listing-item`` block per
listing (rent, bed/bath, availability, truncated description, amenities,
utilities, pet policy) plus a ``markers: [...]`` JSON array with lat/lon.
``/listings/detail/{uid}`` adds the full description, rental terms
(deposit), and the photo gallery; it is refetched only when a listing is new,
changed, or older than ``detail_refresh_hours``.

AppFolio's robots.txt disallows everything; sources using this adapter carry
an explicit ``robots_override`` (see config/sources.toml).
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Iterator

from bs4 import BeautifulSoup

from btv.sources.base import Adapter, ParsedListing, html_to_text, register, to_number

LIST_PATH = "/listings"


def _text(node) -> str | None:
    if node is None:
        return None
    t = re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()
    return t or None


def parse_markers(page: str) -> dict[str, dict]:
    m = re.search(r"markers:\s*(\[.*?\])\s*,\s*\n", page, re.S) or re.search(r"markers:\s*(\[.*?\])", page, re.S)
    if not m:
        return {}
    try:
        markers = json.loads(m.group(1))
    except ValueError:
        return {}
    return {str(mk.get("listing_id")): mk for mk in markers}


def _labelled(block, label: str) -> list[str]:
    """Values after '<span style=bold>Label:</span> a, b, c' in the list blurb."""
    for span in block.find_all("span"):
        if (span.get_text() or "").strip().rstrip(":").lower() == label.lower():
            tail = []
            for sib in span.next_siblings:
                if getattr(sib, "name", None) in ("br",) or (getattr(sib, "name", None) == "span" and sib.find("span")):
                    break
                tail.append(sib.get_text() if hasattr(sib, "get_text") else str(sib))
            return [v.strip() for v in "".join(tail).split(",") if v.strip()]
    return []


def parse_available(text: str | None, today: date | None = None) -> date | None:
    """'10/16/26' -> date; 'NOW' -> None (kept raw; resolved by the date parser)."""
    if not text:
        return None
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", text)
    if not m:
        return None
    mo, d, y = (int(x) for x in m.groups())
    y = y + 2000 if y < 100 else y
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def parse_list_page(page: str) -> list[dict]:
    soup = BeautifulSoup(page, "html.parser")
    markers = parse_markers(page)
    out = []
    for item in soup.select(".js-listing-item"):
        link = item.select_one("a.js-link-to-detail") or item.select_one(".js-listing-title a")
        if link is None:
            continue
        href = link["href"]
        uid = href.rstrip("/").split("/")[-1]
        lid = (item.get("id") or "").replace("listing_", "")
        facts = {}
        for it in item.select(".js-listing-quick-facts .detail-box__item"):
            facts[_text(it.select_one("dt")) or ""] = _text(it.select_one("dd"))
        img = item.select_one(".js-listing-image")
        pet = item.select_one(".js-listing-pet-policy")
        rec = {
            "uid": uid,
            "listing_id": lid,
            "href": href,
            "title": _text(item.select_one(".js-listing-title")),
            "address": _text(item.select_one(".js-listing-address")),
            "rent": facts.get("RENT") or _text(item.select_one(".js-listing-blurb-rent")),
            "bed_bath": facts.get("Bed / Bath") or _text(item.select_one(".js-listing-blurb-bed-bath")),
            "available": facts.get("Available") or _text(item.select_one(".js-listing-available")),
            "sqft": facts.get("Square Feet") or _text(item.select_one(".js-listing-square-feet")),
            "blurb": _text(item.select_one(".js-listing-description")),
            "amenities": _labelled(item, "Amenities"),
            "utilities": _labelled(item, "Utilities Included"),
            "pets": _text(pet).split(":", 1)[-1].strip() if pet else None,
            "image": img.get("data-original") if img else None,
        }
        mk = markers.get(lid) or {}
        rec["lat"], rec["lon"] = mk.get("latitude"), mk.get("longitude")
        out.append(rec)
    return out


def parse_detail_page(page: str) -> dict:
    soup = BeautifulSoup(page, "html.parser")
    desc = soup.select_one(".listing-detail__description")
    terms = [_text(li) for li in soup.select(".js-show-rental-terms li")]
    photos: list[str] = []
    for url in re.findall(r"https://images\.cdn\.appfolio\.com/[^\"' )]+/large\.jpe?g", page):
        if url not in photos:
            photos.append(url)
    lists = {}
    for h3 in soup.select(".listing-detail h3"):
        ul = h3.find_next_sibling("ul")
        if ul:
            lists[_text(h3)] = [_text(li) for li in ul.select("li") if _text(li)]
    phone = None
    m = re.search(r"\(?\d{3}\)?[ .-]\d{3}-\d{4}", soup.get_text(" "))
    if m:
        phone = m.group(0)
    return {
        "description": html_to_text(desc.decode_contents()) if desc else None,
        "rental_terms": [t for t in terms if t],
        "amenities": lists.get("Amenities") or [],
        "utilities": lists.get("Utilities Included") or [],
        "pets": lists.get("Pet Policy") or [],
        "photos": photos,
        "phone": phone,
        "summary": _text(soup.select_one(".js-show-summary")),
    }


def _bed_bath(text: str | None) -> tuple[float | None, float | None]:
    if not text:
        return None, None
    beds = baths = None
    if re.search(r"studio", text, re.I):
        beds = 0.0
    m = re.search(r"([\d.]+)\s*bd", text)
    if m:
        beds = float(m.group(1))
    m = re.search(r"([\d.]+)\s*ba", text)
    if m:
        baths = float(m.group(1))
    return beds, baths


def split_address(addr: str | None) -> tuple[str | None, str | None]:
    """'28 North Main St  - 3, Northfield, VT 05663' -> ('28 North Main St, Northfield, VT 05663', '3')."""
    if not addr:
        return None, None
    addr = re.sub(r"\s+", " ", addr).strip()
    first, _, rest = addr.partition(",")
    street, sep, unit = first.rpartition(" - ")
    if sep and unit.strip():
        unit = re.sub(r"(?i)^(unit|apt\.?|#)\s*", "", unit.strip())
        return f"{street.strip()},{rest}" if rest else street.strip(), unit or None
    return addr, None


def listing_summary(rec: dict) -> dict:
    return {k: v for k, v in rec.items() if k not in ("image",)}


def to_parsed(rec: dict, base_url: str, detail: dict | None) -> ParsedListing:
    detail = detail or {}
    beds, baths = _bed_bath(rec.get("bed_bath"))
    address, unit = split_address(rec.get("address"))
    deposit = None
    for t in detail.get("rental_terms") or []:
        if t.lower().startswith("security deposit"):
            deposit = to_number(t.split(":", 1)[-1], int)
    pets = detail.get("pets") or ([rec["pets"]] if rec.get("pets") else [])
    return ParsedListing(
        external_id=rec["uid"],
        url=base_url + rec["href"],
        title=rec.get("title"),
        address_raw=address,
        unit_raw=unit,
        rent=to_number((rec.get("rent") or "").split("-")[0], int),
        beds=beds,
        baths=baths,
        sqft=to_number(rec.get("sqft"), int),
        avail_structured_raw=rec.get("available"),
        avail_structured_date=parse_available(rec.get("available")),
        description=detail.get("description") or rec.get("blurb"),
        pets="; ".join(pets) or None,
        utilities=", ".join(detail.get("utilities") or rec.get("utilities") or []) or None,
        amenities=detail.get("amenities") or rec.get("amenities") or [],
        photos=detail.get("photos") or ([rec["image"]] if rec.get("image") else []),
        contact_raw={"phone": detail["phone"]} if detail.get("phone") else None,
        extra={
            "platform": "appfolio",
            "appfolio": listing_summary(rec),
            "lat": rec.get("lat"),
            "lon": rec.get("lon"),
            "deposit": deposit,
            "rental_terms": detail.get("rental_terms") or [],
        },
    )


@register
class AppfolioAdapter(Adapter):
    platform = "appfolio"
    parser_version = "appfolio-1"

    def run(self, ctx) -> Iterator[ParsedListing]:
        base = self.config["base_url"].rstrip("/")
        if not base:
            raise ValueError("base_url not configured")
        want_details = bool(self.config.get("fetch_details", True))
        ctx.step("fetching listings page")
        res, fetch_id = self.fetcher.get(base + LIST_PATH)
        if "js-listings-container" not in res.text:
            raise ValueError("AppFolio listings container missing (layout changed?)")
        records = parse_list_page(res.text)
        ctx.set_total(len(records) + 1)
        ctx.advance(step=f"found {len(records)} listings")
        for rec in records:
            detail, reused = None, False
            if want_details:
                detail_url = base + rec["href"]
                prev = self.previous_snapshot(rec["uid"])
                prev_summary = (prev.extra or {}).get("appfolio") if prev else None
                if prev and self.detail_is_fresh(detail_url, prev_summary, listing_summary(rec)):
                    detail = {
                        "description": prev.description, "amenities": prev.amenities, "photos": prev.photos,
                        "utilities": [u.strip() for u in (prev.utilities or "").split(",") if u.strip()],
                        "pets": [p for p in (prev.pets or "").split("; ") if p],
                        "rental_terms": (prev.extra or {}).get("rental_terms") or [],
                        "phone": (prev.contact_raw or {}).get("phone"),
                    }
                    reused = True
                else:
                    ctx.step(f"detail {rec.get('address') or rec['uid']}")
                    d_res, _ = self.fetcher.get(detail_url)
                    detail = parse_detail_page(d_res.text)
            pl = to_parsed(rec, base, detail)
            pl.raw_fetch_id = fetch_id
            yield pl
            ctx.advance(step=f"{'cached' if reused else 'parsed'} {pl.address_raw or rec['uid']}")
