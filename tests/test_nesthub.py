import json
from datetime import date

import httpx

from btv.jobs import runner
from btv.sources import nesthub

from .conftest import FIXTURES, add_source

BASE = "https://www.burlingtonproperty.management"
LISTINGS = json.loads((FIXTURES / "nesthub/fiveseasons_listings.json").read_text())
DETAIL = json.loads((FIXTURES / "nesthub/fiveseasons_listing_208.json").read_text())


def handler(req):
    if req.url.path == "/_system/api/listings":
        return httpx.Response(200, json=LISTINGS)
    if req.url.path.startswith("/_system/api/listings/"):
        return httpx.Response(200, json=DETAIL)
    return httpx.Response(404)


def test_split_unit():
    assert nesthub.split_unit("133 Elmwood Avenue - 4") == ("133 Elmwood Avenue", "4")
    assert nesthub.split_unit("49 Intervale Avenue - 47 A") == ("49 Intervale Avenue", "47 A")
    assert nesthub.split_unit("27 Nash Place") == ("27 Nash Place", None)


def test_to_parsed_fields():
    pl = nesthub.to_parsed(LISTINGS["listings"][0], BASE, None)
    assert pl.url == f"{BASE}/_system/listings/208/133-Elmwood-Avenue---4-Burlington-VT-05401-US"
    assert pl.address_raw == "133 Elmwood Avenue, Burlington, VT 05401"
    assert (pl.unit_raw, pl.rent, pl.beds, pl.sqft) == ("4", 1600, 1.0, 600)
    assert pl.avail_structured_date == date(2026, 10, 1)
    assert "Balcony, deck, patio" in pl.amenities
    assert pl.extra["lat"] and pl.extra["lon"]
    assert "<br" not in pl.description


def test_scrape_end_to_end(cfg, mock_http):
    mock_http["handler"] = handler
    add_source(cfg, "fiveseasons", "nesthub", base_url=BASE)
    j = runner.run_inline("scrape", source_id="fiveseasons", cfg=cfg)
    assert j.status == "succeeded", j.error
    assert j.result["listings"] == 28 and j.result["healthy"]
    assert len(mock_http["log"]) == 1 + 1 + 28
    mock_http["log"].clear()
    runner.run_inline("scrape", source_id="fiveseasons", cfg=cfg)
    assert len(mock_http["log"]) == 2
