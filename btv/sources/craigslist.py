"""Craigslist apartments/housing for rent (``apa``) around a postal code.

* Search: ``sapi.craigslist.org/web/v8/postings/search/full`` (the JSON the
  site's own search page uses) returns every active posting in the radius
  with price, beds, sqft, lat/lon, title, slug and image ids.
* Detail: ``{area}.craigslist.org/apa/d/{slug}/{id}.html`` gives the body,
  map address, attributes (laundry, parking, pets, "available oct 1") and
  posted/updated times. Fetched only for new or changed postings.

Contact info behind "show contact info" lives under ``/reply`` which
robots.txt disallows; it is never fetched. Phones/emails that posters put
in the body are picked up by the normal contact extraction.

Craigslist postings are mostly private landlords (and scammers); they are
tagged ``extra.private_landlord`` unless the text names a management company.
"""

from __future__ import annotations

import html as _html
import re
from datetime import datetime
from typing import Iterator
from urllib.parse import urlencode

from btv.sources.base import Adapter, ParsedListing, html_to_text, register, to_number

SEARCH = "https://sapi.craigslist.org/web/v8/postings/search/full"
IMG = "https://images.craigslist.org/{}_600x450.jpg"
_PM_WORDS = re.compile(r"(?i)\b(property\s+management|management\s+(?:co|company|group)|realty|leasing\s+office|apartments?\s+(?:llc|inc)|llc\b|professionally\s+managed)")


def decode_items(data: dict, area: str) -> list[dict]:
    dec = data.get("decode") or {}
    min_id = int(dec.get("minPostingId") or 0)
    min_date = int(dec.get("minPostedDate") or 0)
    locs = dec.get("locationDescriptions") or []
    out = []
    for it in data.get("items") or []:
        if not isinstance(it, list) or len(it) < 4:
            continue
        rec = {"posting_id": str(min_id + int(it[0])), "posted_ts": min_date + int(it[1]), "price": it[3],
               "images": [], "slug": None, "title": None, "beds": None, "sqft": None, "lat": None, "lon": None,
               "location": None}
        for el in it[4:]:
            if isinstance(el, str):
                m = re.match(r"^(\d+):(\d+)~(-?[\d.]+)~(-?[\d.]+)$", el)
                if m:
                    idx = int(m.group(2))
                    rec["location"] = locs[idx] if 0 < idx < len(locs) else None
                    rec["lat"], rec["lon"] = float(m.group(3)), float(m.group(4))
                elif rec["title"] is None and not re.fullmatch(r"[0-9A-Za-z_]{6,}", el):
                    rec["title"] = el
            elif isinstance(el, list) and el:
                tag = el[0]
                if tag == 4:
                    rec["images"] = [IMG.format(x.split(":", 1)[-1]) for x in el[1:] if isinstance(x, str)]
                elif tag == 6 and len(el) > 1:
                    rec["slug"] = el[1]
                elif tag == 5 and len(el) > 2:
                    rec["beds"], rec["sqft"] = el[1], el[2] or None
                elif tag == 10 and len(el) > 1:
                    rec["price_text"] = el[1]
        if rec["slug"]:
            rec["url"] = f"https://{area}.craigslist.org/apa/d/{rec['slug']}/{rec['posting_id']}.html"
            out.append(rec)
    return out


def _text(pattern: str, s: str, flags=re.S) -> str | None:
    m = re.search(pattern, s, flags)
    return _html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1)))).strip() if m else None


def parse_detail(page: str) -> dict:
    body = None
    m = re.search(r'<section id="postingbody">(.*?)</section>', page, re.S)
    if m:
        b = re.sub(r'<div class="print-information.*?</div>\s*</div>', "", m.group(1), flags=re.S)
        b = re.sub(r'<a [^>]*class="show-contact"[^>]*>.*?</a>', "", b, flags=re.S)
        body = html_to_text(b)
    attrs = [_html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", a))).strip()
             for a in re.findall(r'<div class="attr[^"]*">(.*?)</div>', page, re.S)]
    important = _text(r'<span class="attr important">(.*?)</span>', page)
    lat = re.search(r'data-latitude="(-?[\d.]+)"', page)
    lon = re.search(r'data-longitude="(-?[\d.]+)"', page)
    times = re.findall(r'<time class="date timeago" datetime="([^"]+)"', page)
    return {
        "body": body,
        "map_address": _text(r'<div class="mapaddress">(.*?)</div>', page),
        "attrs": [a for a in attrs if a],
        "bed_bath": important,
        "lat": float(lat.group(1)) if lat else None,
        "lon": float(lon.group(1)) if lon else None,
        "posted": times[0] if times else None,
        "updated": times[-1] if len(times) > 1 else None,
        "title": _text(r'id="titletextonly">(.*?)</span>', page),
    }


def _attr(attrs: list[str], *keys: str) -> str | None:
    for a in attrs:
        low = a.lower()
        if any(k in low for k in keys):
            return a.split(":", 1)[-1].strip()
    return None


def to_parsed(rec: dict, detail: dict | None, city_default: str) -> ParsedListing:
    d = detail or {}
    attrs = d.get("attrs") or []
    baths = None
    m = re.search(r"([\d.]+)\s*Ba", d.get("bed_bath") or "", re.I)
    if m:
        baths = float(m.group(1))
    avail_raw = next((a for a in attrs if a.lower().startswith("available")), None)
    loc = (rec.get("location") or "").strip()
    address = d.get("map_address")
    if address and not re.search(r"\b(vt|vermont)\b", address, re.I):
        town = loc if loc and not re.search(r"\d", loc) else city_default
        address = f"{address}, {town}, VT"
    description = d.get("body")
    pets = _attr(attrs, "cats", "dogs", "pet")
    if pets is None:
        pets = "; ".join(a for a in attrs if re.search(r"(?i)cats? are ok|dogs? are ok", a)) or None
    laundry = next((a for a in attrs if "laundry" in a.lower() or "w/d" in a.lower()), None)
    parking = next((a for a in attrs if re.search(r"(?i)garage|parking|carport", a)), None)
    blob = f"{rec.get('title') or ''}\n{description or ''}"
    return ParsedListing(
        external_id=rec["posting_id"],
        url=rec["url"],
        title=d.get("title") or rec.get("title"),
        address_raw=address,
        unit_raw=None,
        rent=to_number(rec.get("price"), int),
        beds=to_number(rec.get("beds")),
        baths=baths,
        sqft=to_number(rec.get("sqft"), int),
        avail_structured_raw=avail_raw,
        avail_structured_date=None,
        description=description,
        pets=pets,
        parking=parking,
        amenities=[x for x in (laundry,) if x],
        photos=rec.get("images") or [],
        contact_raw=None,
        extra={
            "platform": "craigslist",
            "craigslist": {k: v for k, v in rec.items() if k not in ("images",)},
            "lat": d.get("lat") or rec.get("lat"),
            "lon": d.get("lon") or rec.get("lon"),
            "approx_location": loc or None,
            "attrs": attrs,
            "posted_at": d.get("posted") or datetime.utcfromtimestamp(rec["posted_ts"]).isoformat(),
            "updated_at": d.get("updated"),
            "private_landlord": not bool(_PM_WORDS.search(blob)),
            "address_from": "map" if d.get("map_address") else None,
        },
    )


@register
class CraigslistAdapter(Adapter):
    platform = "craigslist"
    parser_version = "craigslist-1"

    def run(self, ctx) -> Iterator[ParsedListing]:
        area = self.config.get("area", "burlington")
        params = {"batch": "1-0-360-0-0", "cc": "US", "lang": "en", "searchPath": self.config.get("category", "apa"),
                  "postal": self.config.get("postal", "05401"),
                  "search_distance": self.config.get("search_distance", 12)}
        ctx.step("searching craigslist")
        res, fetch_id = self.fetcher.get(f"{SEARCH}?{urlencode(params)}", headers={"Accept": "application/json"})
        data = res.json().get("data") or {}
        records = decode_items(data, area)
        if not records and data.get("totalResultCount"):
            raise ValueError("craigslist search returned results we could not decode (format changed?)")
        ctx.set_total(len(records) + 1)
        ctx.advance(step=f"found {len(records)} postings")
        city_default = self.config.get("default_city", "Burlington")
        for rec in records:
            prev = self.previous_snapshot(rec["posting_id"])
            summary = {k: rec.get(k) for k in ("price", "title", "beds", "sqft", "slug")}
            prev_summary = {k: ((prev.extra or {}).get("craigslist") or {}).get(k) for k in summary} if prev else None
            detail, reused = None, False
            if prev and self.detail_is_fresh(rec["url"], prev_summary, summary):
                reused = True
                pl = ParsedListing(**{**{k: getattr(prev, k) for k in (
                    "title", "address_raw", "unit_raw", "rent", "beds", "baths", "sqft", "avail_structured_raw",
                    "avail_structured_date", "description", "pets", "parking", "utilities", "amenities", "photos",
                    "contact_raw")}, "external_id": rec["posting_id"], "url": rec["url"], "extra": prev.extra or {}})
            else:
                ctx.step(f"post {rec.get('title') or rec['posting_id']}")
                try:
                    d_res, _ = self.fetcher.get(rec["url"])
                    detail = parse_detail(d_res.text)
                except Exception as exc:  # noqa: BLE001 - a deleted post shouldn't fail the run
                    ctx.log(f"detail failed for {rec['url']}: {exc}", level="warn")
                pl = to_parsed(rec, detail, city_default)
            pl.raw_fetch_id = fetch_id
            yield pl
            ctx.advance(step=f"{'cached' if reused else 'parsed'} {pl.title or rec['posting_id']}")
