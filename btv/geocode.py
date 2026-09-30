"""Geocode buildings that the source didn't give coordinates for.

US Census geocoder first (free, no key), Nominatim as fallback at 1 req/s
with an identifying User-Agent per its usage policy. Results are stored on
the building (so each address is geocoded once); failures are recorded as
``geocode_source='failed'`` and retried by ``btv geocode --retry-failed``.
"""

from __future__ import annotations

from dataclasses import replace
from urllib.parse import urlencode

from btv.db import session_factory, utcnow
from btv.http import FetchError, PoliteClient
from btv.jobs.runner import JobContext, job
from btv.models import Building

CENSUS = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
NOMINATIM_UA = "BTVAPT/0.1 (personal apartment search; single user, low volume)"


def one_line(b: Building) -> str:
    street = " ".join(p for p in (b.street_number, b.street) if p)
    city = (b.city or "burlington").title()
    return f"{street}, {city}, {(b.state or 'vt').upper()}{' ' + b.zip if b.zip else ''}"


def census(client: PoliteClient, address: str) -> tuple[float, float] | None:
    q = urlencode({"address": address, "benchmark": "Public_AR_Current", "format": "json"})
    data = client.get(f"{CENSUS}?{q}").json()
    matches = (data.get("result") or {}).get("addressMatches") or []
    if not matches:
        return None
    c = matches[0]["coordinates"]
    return float(c["y"]), float(c["x"])


def nominatim(client: PoliteClient, address: str) -> tuple[float, float] | None:
    q = urlencode({"q": address, "format": "json", "limit": 1, "countrycodes": "us"})
    data = client.get(f"{NOMINATIM}?{q}", headers={"User-Agent": NOMINATIM_UA}).json()
    if not data:
        return None
    return float(data[0]["lat"]), float(data[0]["lon"])


@job("geocode")
def geocode_job(ctx: JobContext, retry_failed: bool = False, **_ignored) -> dict:
    session = session_factory(ctx.cfg)()
    client = PoliteClient(replace(ctx.cfg, per_host_interval=1.2), respect_robots=False)
    stats = {"census": 0, "nominatim": 0, "failed": 0}
    try:
        q = session.query(Building).filter(Building.lat.is_(None))
        if not retry_failed:
            q = q.filter(Building.geocode_source.is_(None))
        todo = q.all()
        ctx.set_total(len(todo))
        for b in todo:
            addr = one_line(b)
            ctx.step(f"geocoding {addr}")
            found, how = None, "failed"
            for name, fn in (("census", census), ("nominatim", nominatim)):
                try:
                    found = fn(client, addr)
                except (FetchError, ValueError, KeyError) as exc:
                    ctx.log(f"{name} failed for {addr}: {exc}", level="warn")
                    found = None
                if found:
                    how = name
                    break
            if found:
                b.lat, b.lon = found
            b.geocode_source, b.geocoded_at = how, utcnow()
            stats[how] += 1
            session.commit()
            ctx.advance()
        return stats
    finally:
        client.close()
        session.close()
