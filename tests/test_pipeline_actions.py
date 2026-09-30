from fastapi.testclient import TestClient

from btv import actions, views
from btv.db import session_scope
from btv.jobs import runner
from btv.models import Contact, SourceListing, Source, Unit

from .conftest import listing


def scrape(cfg, sid="demo"):
    j = runner.run_inline("scrape", source_id=sid, cfg=cfg)
    assert j.status == "succeeded", j.error
    return j.result


def second_source(cfg, tmp_path, items):
    import json
    p = tmp_path / "second.json"
    p.write_text(json.dumps(items))
    with session_scope(cfg) as s:
        s.add(Source(id="agg", name="Aggregator", platform="file", config={"path": str(p)}, interval_minutes=60))
    return p


def unit_of(cfg, source_id, ext):
    with session_scope(cfg) as s:
        return s.query(SourceListing).filter_by(source_id=source_id, external_id=ext).one().unit_id


def test_cross_source_dedupe_and_text_date_wins(cfg, file_source, tmp_path):
    file_source([listing("a", address_raw="46 Lafountain St Apt 1, Burlington, VT", avail_structured_date="2025-06-01",
                         description="Available June 1st, 2027: bright unit with washer and dryer in unit.")])
    second_source(cfg, tmp_path, [listing("x", address_raw="46 Lafountain Street #1, Burlington, VT 05401", rent=1550)])
    scrape(cfg)
    scrape(cfg, "agg")
    assert unit_of(cfg, "demo", "a") == unit_of(cfg, "agg", "x")
    with session_scope(cfg) as s:
        data = views.list_units(s)
    assert data["count"] == 1
    u = data["units"][0]
    assert len(u["sources"]) == 2 and all(x["url"] for x in u["sources"])
    assert u["availability"]["effective_date"] == "2027-06-01" and u["availability"]["match"] == "exact"
    assert any(a["tag"] == "washer/dryer in unit" for a in u["text_amenities"])


def test_prefs_survive_relist_and_merge_with_undo(cfg, file_source, tmp_path):
    file_source([listing("a", address_raw="10 Isham St, Burlington, VT"), listing("b", address_raw="12 Isham St, Burlington, VT")])
    scrape(cfg)
    ua, ub = unit_of(cfg, "demo", "a"), unit_of(cfg, "demo", "b")
    with session_scope(cfg) as s:
        actions.set_prefs(s, ua, rating=5, status="interested", notes="roof access!")
        actions.add_tag(s, ua, "Roof Access")
        actions.add_tag(s, ub, "balcony")
    # Relist under a new external id: same address -> same unit -> notes intact.
    file_source([listing("a2", address_raw="10 Isham Street, Burlington, VT 05401"), listing("b", address_raw="12 Isham St, Burlington, VT")])
    scrape(cfg)
    assert unit_of(cfg, "demo", "a2") == ua
    with session_scope(cfg) as s:
        log = actions.merge_units(s, ua, ub, "same place")
        log_id = log.id
    with session_scope(cfg) as s:
        d = views.unit_detail(s, ub)  # old id resolves to the merged unit
        assert d["id"] == ua and d["prefs"]["rating"] == 5 and set(d["tags"]) == {"roof access", "balcony"}
        assert len(d["sources"]) == 3
    with session_scope(cfg) as s:
        actions.undo(s, log_id)
    with session_scope(cfg) as s:
        assert s.get(Unit, ub).merged_into_id is None
        assert views.unit_detail(s, ua)["tags"] == ["roof access"]


def test_unlink_listing_and_lock(cfg, file_source, tmp_path):
    file_source([listing("a", address_raw="5 Main St, Burlington, VT")])
    second_source(cfg, tmp_path, [listing("x", address_raw="5 Main Street, Burlington, VT")])
    scrape(cfg)
    scrape(cfg, "agg")
    sl_id = None
    with session_scope(cfg) as s:
        sl_id = s.query(SourceListing).filter_by(source_id="agg").one().id
        actions.unlink_listing(s, sl_id, "different unit")
    scrape(cfg, "agg")  # auto-linking must respect the lock
    assert unit_of(cfg, "demo", "a") != unit_of(cfg, "agg", "x")


def test_contact_graph_links_across_listings(cfg, file_source):
    file_source([
        listing("a", address_raw="1 Pine St, Burlington, VT", description="Call Jane Smith at 802-555-1234."),
        listing("b", address_raw="9 Oak St, Burlington, VT", description="Questions? Text 802 555 1234 anytime."),
        listing("c", address_raw="3 Elm St, Burlington, VT", description="Contact J. Smith for a showing."),
    ])
    scrape(cfg)
    with session_scope(cfg) as s:
        cs = views.list_contacts(s)
        jane = next(c for c in cs if c["name"] == "Jane Smith")
        assert jane["unit_count"] == 3 and jane["building_count"] == 3 and jane["multi_property"]
        assert jane["phones"] == ["(802) 555-1234"]
        other = s.query(Contact).filter(Contact.id != jane["id"]).count()
        assert other == 0


def test_api_units_contacts_actions(cfg, file_source):
    from btv.api.app import create_app

    file_source([listing("a", address_raw="1 Pine St, Burlington, VT", rent=1800, beds=2,
                         description="Available June 1st, 2027. Call Jane Smith at 802-555-1234.")])
    scrape(cfg)
    with TestClient(create_app(cfg, start_worker=False)) as c:
        units = c.get("/api/units?match=exact,near&max_rent=2000").json()
        assert units["count"] == 1
        uid = units["units"][0]["id"]
        assert c.put(f"/api/units/{uid}/prefs", json={"rating": 4, "status": "toured"}).json()["rating"] == 4
        assert c.put(f"/api/units/{uid}/prefs", json={"rating": 9}).status_code == 400
        assert c.post(f"/api/units/{uid}/tags", json={"tag": "W/D"}).json()["tags"] == ["w/d"]
        assert c.get("/api/units?tag=w/d").json()["count"] == 1
        d = c.get(f"/api/units/{uid}").json()
        assert d["contacts"][0]["name"] == "Jane Smith" and d["turnover"]["predicted_next_available"] == "2028-06-01"
        cid = d["contacts"][0]["id"]
        assert c.post(f"/api/contacts/{cid}/outreach", json={"channel": "call", "summary": "asked about June"}).status_code == 200
        cd = c.get(f"/api/contacts/{cid}").json()
        assert cd["outreach"][0]["summary"] == "asked about June" and cd["units"][0]["id"] == uid
        # Changing the target date re-classifies at view time.
        c.put("/api/settings", json={"target_move_in": "2027-09-01", "near_miss_days": 30})
        assert c.get(f"/api/units/{uid}").json()["availability"]["match"] == "early"
