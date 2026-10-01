import json

import httpx
from fastapi.testclient import TestClient

from btv import views
from btv.db import session_scope
from btv.jobs import runner
from btv.models import BuildingOwner, Company, Source
from btv.normalize.freeform import parse_freeform
from btv.normalize.scam import ScamIndex, text_flags
from btv.owners import owner_kind, parse_search
from btv.sources import craigslist

from .conftest import FIXTURES, add_source

CL_SEARCH = json.loads((FIXTURES / "craigslist/search_05401.json").read_text())
CL_DETAIL = (FIXTURES / "craigslist/detail_7967099187.html").read_text()
SMTR = (FIXTURES / "showmetherent/burlington.html").read_text()


def test_craigslist_decode_and_detail():
    recs = craigslist.decode_items(CL_SEARCH["data"], "burlington")
    assert len(recs) == 255 and all(r["lat"] and r["url"].endswith(".html") for r in recs)
    r = next(x for x in recs if x["posting_id"] == "7967099187")
    assert r["price"] == 2200 and r["beds"] == 2
    d = craigslist.parse_detail(CL_DETAIL)
    pl = craigslist.to_parsed(r, d, "Burlington")
    assert pl.address_raw == "75 Shelburne rd, Burlington, VT"
    assert (pl.rent, pl.beds, pl.baths) == (2200, 2.0, 1.0)
    assert "available immediately" in pl.description and "show contact info" not in pl.description
    assert pl.extra["private_landlord"] is True


def test_craigslist_scrape_never_touches_reply(cfg, mock_http):
    def handler(req):
        if req.url.host == "sapi.craigslist.org":
            data = {"data": dict(CL_SEARCH["data"], items=CL_SEARCH["data"]["items"][:3])}
            return httpx.Response(200, json=data)
        return httpx.Response(200, text=CL_DETAIL)

    mock_http["handler"] = handler
    add_source(cfg, "craigslist", "craigslist", postal="05401")
    j = runner.run_inline("scrape", source_id="craigslist", cfg=cfg)
    assert j.status == "succeeded", j.error
    assert j.result["listings"] == 3
    assert not any("/reply" in u for u in mock_http["log"])


def test_freeform():
    f = parse_freeform("Subletting! 2br/1ba at 54 North Union St, Burlington. $975/month, available June 1st. Cats ok.")
    assert (f["rent"], f["beds"], f["baths"], f["pets"]) == (975, 2.0, 1.0, "pets ok")
    assert f["address"] == "54 North Union St, Burlington"
    assert parse_freeform("3 bedroom house. Rent is 2,850. No pets.")["rent"] == 2850
    f = parse_freeform("Moving out of our 2 bedroom on Loomis St (32 Loomis St, Burlington), $1,950/month")
    assert f["address"] == "32 Loomis St, Burlington"
    assert parse_freeform("Cozy 2 Bedroom Apt near campus, 5 Minutes walk")["address"] is None


def test_scam_text_flags():
    flags = text_flags("I'm currently out of the country on a mission trip. Keys will be mailed once you send the deposit via Zelle.")
    reasons = " | ".join(r for _, r, _ in flags)
    assert "away" in reasons and "keys" in reasons.lower() and sum(p for p, _, _ in flags) >= 5
    assert text_flags("Lovely 2br, call to schedule a showing.") == []


def _unit(uid, platform, rent, beds=2, desc_owner=None, photos=()):
    return {"id": uid, "rent": rent, "beds": beds, "status": "available", "title": "", "address": f"{uid} Main St",
            "lat": 44.47, "sources": [{"platform": platform, "source": platform}]}


def test_scam_copied_listing_and_low_price():
    real_text = ("Sunny two bedroom apartment on a quiet street near downtown with hardwood floors, "
                 "a big porch, off street parking and laundry in the basement. Heat and hot water included.")
    units = [_unit(1, "appfolio", 2400)] + [_unit(10 + i, "appfolio", 2300 + i * 10) for i in range(5)] + [_unit(2, "craigslist", 1100)]
    descs = {u["id"]: None for u in units}
    descs[1] = real_text
    descs[2] = real_text + " Email me directly at owner@gmail.com, I am out of the country."
    photos = {u["id"]: [] for u in units}
    photos[1] = ["https://images.cdn.appfolio.com/x/images/abc/large.jpeg"]
    idx = ScamIndex(units, descs, photos)
    r = idx.assess(units[-1], descs[2], ["https://images.cdn.appfolio.com/x/images/abc/medium.jpeg"])
    kinds = {x["kind"] for x in r["reasons"]}
    assert r["level"] == "high" and {"copy", "price", "text"} <= kinds
    assert idx.assess(units[0], descs[1], photos[1])["level"] == "verified"


def test_owners_parse_and_link(cfg, monkeypatch):
    listings = parse_search(SMTR)
    assert len(listings) > 100
    assert owner_kind("375 North Ave LLC") == "owner_llc" and owner_kind("JOHNNY BUSHEY") == "owner"
    import btv.owners as owners_mod

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def get(self, url):
            return type("R", (), {"text": SMTR})()

        def close(self):
            pass

    monkeypatch.setattr(owners_mod, "PoliteClient", FakeClient)
    add_source(cfg, "bissonette", "appfolio", base_url="https://bissonetteproperties.appfolio.com")
    j = runner.run_inline("owners", params={"towns": ["Burlington"]}, cfg=cfg)
    assert j.status == "succeeded", j.error
    with session_scope(cfg) as s:
        names = {c.name for c in s.query(Company).filter(Company.kind.like("owner%"))}
        assert "375 North Ave LLC" in names
        assert not any("Bissonette" in n for n in names)  # manager-named entries are skipped
        assert s.query(BuildingOwner).count() > 20
        owners = views.list_owners(s)
        assert owners and owners[0]["building_count"] >= 1


def test_leads_api_flow(cfg):
    from btv.api.app import create_app

    with session_scope(cfg) as s:
        s.add(Source(id="manual", name="Saved by me", platform="manual", config={}, interval_minutes=1440))
    with TestClient(create_app(cfg, start_worker=False)) as c:
        text = ("Hey all, my landlord has a 2 bedroom opening up at 54 North Union St, Burlington, available June 1st 2027. "
                "$1,900/month. Text Jane Smith 802-555-0199.")
        r = c.post("/api/leads", json={"text": text, "url": "https://www.facebook.com/groups/123/posts/456", "author": "Sam"})
        assert r.status_code == 200 and r.json()["unit_id"]
        leads = c.get("/api/leads?triage=new").json()
        assert leads[0]["rent"] == 1900 and leads[0]["availability"]["date"] == "2027-06-01"
        u = c.get(f"/api/units/{r.json()['unit_id']}").json()
        assert u["private_landlord"] and u["availability"]["match"] == "exact"
        assert any(ct["name"] == "Jane Smith" and ct["private"] for ct in u["contacts"])
        # A post without an address can be placed by hand.
        r2 = c.post("/api/leads", json={"text": "Room for rent, $800, available August. DM me."}).json()
        assert r2["unit_id"] is None
        placed = c.put(f"/api/leads/{r2['source_listing_id']}", json={"address": "12 Pearl St, Burlington, VT", "triage": "saved"}).json()
        assert placed["unit_id"] and placed["triage"] == "saved"
        assert c.post("/api/leads", json={"text": ""}).status_code == 400
