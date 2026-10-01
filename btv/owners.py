"""Owner entities behind buildings, from ShowMeTheRent (AppFolio's public
rental search).

Each ShowMeTheRent listing names the *owner entity* AppFolio has on file
("375 North Ave LLC", "Sunderland Farms LLC", sometimes a person) plus the
managing company's AppFolio host. We attach those owners to buildings so the
contacts view can show who actually owns a place, not just who manages it.

Only public listing data is used. One request per town per run.
"""

from __future__ import annotations

import json
import re

from btv.db import session_factory, utcnow
from btv.http import PoliteClient
from btv.jobs.runner import JobContext, job
from btv.models import BuildingOwner, Company, Source
from btv.normalize.address import normalize_address
from btv.normalize.contacts import normalize_name, normalize_phone
from btv.pipeline import find_or_create_building

BASE = "https://www.showmetherent.com/listings/VT/{city}?latitude={lat}&longitude={lon}"
TOWNS = {
    "Burlington": (44.4759, -73.2121),
    "South-Burlington": (44.4669, -73.1709),
    "Winooski": (44.4914, -73.1857),
    "Essex-Junction": (44.4906, -73.1110),
    "Colchester": (44.5439, -73.1479),
    "Williston": (44.4376, -73.0682),
    "Shelburne": (44.3770, -73.2273),
}
_ENTITY = re.compile(r"(?i)\b(llc|l\.l\.c|inc|corp|corporation|partnership|trust|associates|holdings|ltd|lp)\b")


def parse_search(page: str) -> list[dict]:
    m = re.search(r"window\.__remixContext = (\{.*?\});</script>", page, re.S)
    if not m:
        raise ValueError("ShowMeTheRent page layout changed (no __remixContext)")
    ctx = json.loads(m.group(1))
    out = []
    for route, data in (ctx.get("state", {}).get("loaderData") or {}).items():
        if isinstance(data, dict) and isinstance(data.get("listings"), list):
            out.extend(data["listings"])
    return out


def owner_kind(name: str) -> str:
    return "owner_llc" if _ENTITY.search(name) else "owner"


def _manager_hosts(session) -> dict[str, Source]:
    hosts = {}
    for src in session.query(Source).all():
        m = re.match(r"https?://([a-z0-9-]+)\.appfolio\.com", (src.config or {}).get("base_url") or "")
        if m:
            hosts[m.group(1).lower()] = src
    return hosts


def _is_manager_name(name: str, managers: list[str]) -> bool:
    n = normalize_name(name) or ""
    if re.search(r"(?i)property management|properties\b|management,? inc", name):
        return True
    return any(n and (n in m or m in n) for m in managers)


@job("owners")
def owners_job(ctx: JobContext, towns: list[str] | None = None, **_ignored) -> dict:
    session = session_factory(ctx.cfg)()
    client = PoliteClient(ctx.cfg)
    stats = {"listings": 0, "owners": 0, "links": 0, "skipped_manager_named": 0}
    try:
        towns = towns or list(TOWNS)
        hosts = _manager_hosts(session)
        manager_names = [normalize_name(s.name) or "" for s in session.query(Source).all()]
        ctx.set_total(len(towns))
        for town in towns:
            lat, lon = TOWNS.get(town, TOWNS["Burlington"])
            ctx.step(f"ShowMeTheRent: {town.replace('-', ' ')}")
            res = client.get(BASE.format(city=town, lat=lat, lon=lon))
            for item in parse_search(res.text):
                stats["listings"] += 1
                comp = item.get("company_details") or {}
                name = (comp.get("name") or "").strip()
                loc = item.get("location") or {}
                if not name or not loc.get("street_address"):
                    continue
                if _is_manager_name(name, manager_names):
                    stats["skipped_manager_named"] += 1
                    continue
                na = normalize_address(f"{loc['street_address']}, {loc.get('city') or ''}, {loc.get('state') or 'VT'} {loc.get('postal_code') or ''}")
                if na is None or not na.street:
                    continue
                building = find_or_create_building(session, na, loc.get("latitude"), loc.get("longitude"))
                host = next(iter(re.findall(r"images\.cdn\.appfolio\.com/([a-z0-9]+)/", json.dumps(item))), None)
                manager_src = hosts.get((host or "").lower())
                manager_co = None
                if manager_src is not None:
                    from btv.pipeline import company_for_source

                    manager_co = company_for_source(session, manager_src)
                norm = normalize_name(name) or name.lower()
                owner = session.query(Company).filter(Company.norm_name == norm, Company.kind.like("owner%")).first()
                if owner is None:
                    owner = Company(name=name, norm_name=norm, kind=owner_kind(name))
                    session.add(owner)
                    session.flush()
                    stats["owners"] += 1
                link = (session.query(BuildingOwner)
                        .filter_by(building_id=building.id, company_id=owner.id, source="showmetherent").one_or_none())
                evidence = {"listing_url": item.get("url"), "listing_name": item.get("listing_name"),
                            "manager_host": host, "available_units": item.get("available_unit_count")}
                if link is None:
                    session.add(BuildingOwner(building_id=building.id, company_id=owner.id,
                                              manager_company_id=manager_co.id if manager_co else None,
                                              source="showmetherent", phone=normalize_phone(comp.get("phone")),
                                              evidence=evidence))
                    stats["links"] += 1
                else:
                    link.last_seen, link.evidence = utcnow(), evidence
                    if manager_co:
                        link.manager_company_id = manager_co.id
                session.commit()
            ctx.advance()
        return stats
    finally:
        client.close()
        session.close()
