from datetime import date

import httpx

from btv.db import session_scope
from btv.jobs import runner
from btv.models import ListingSnapshot, SourceListing
from btv.sources import buildium

from .conftest import FIXTURES, add_source

BASE = "https://hinsdaleproperties.managebuilding.com"
SEARCH = (FIXTURES / "buildium/hinsdale_search.html").read_text()
DETAIL = (FIXTURES / "buildium/hinsdale_detail_110169.html").read_text()
IMAGES = (FIXTURES / "buildium/hinsdale_images_110169.html").read_text()


def handler(req):
    path = req.url.path.lower()
    if path.endswith("apartmentsearch.aspx"):
        return httpx.Response(200, text=SEARCH)
    if path.endswith("apartmentdetail.aspx"):
        return httpx.Response(200, text=DETAIL)
    if path.endswith("apartmentimages.aspx"):
        return httpx.Response(200, text=IMAGES)
    return httpx.Response(404)


def test_parse_search_page():
    recs = buildium.parse_search_page(SEARCH)
    assert len(recs) == 8
    assert {r["listingid"] for r in recs} >= {"110169", "12795"}
    assert all(r["unitdescription"] for r in recs)


def test_to_parsed_fields():
    rec = buildium.parse_search_page(SEARCH)[0]
    pl = buildium.to_parsed(rec, BASE, buildium.parse_detail_page(DETAIL), buildium.parse_images_page(IMAGES))
    assert pl.url == f"{BASE}/Resident/PublicPages/apartmentdetail.aspx?listingId=110169&unitid=59113&buildingid=21011"
    assert pl.address_raw == "122 North Winooski Avenue, Apt. #3, Burlington, VT 05401"
    assert (pl.unit_raw, pl.rent, pl.beds, pl.baths) == ("#3", 1950, 2.0, 1.0)
    # Structured field is stale; the description carries the real date (parsed in step 3).
    assert pl.avail_structured_date == date(2025, 6, 1)
    assert pl.description.startswith("Available June 1st, 2027")
    assert "Balcony, deck, patio" in pl.amenities
    assert len(pl.photos) >= 10
    assert pl.contact_raw["phone"] == "(802) 731-0170"
    assert pl.extra["deposit"] == 1950 and pl.extra["year_built"] == 1899


def test_dates():
    assert buildium.parse_buildium_date("Friday, December 31, 9999") is None
    assert buildium.parse_buildium_date("Tuesday, June 1, 2027") == date(2027, 6, 1)
    assert buildium.parse_buildium_date("") is None


def test_scrape_end_to_end_and_incremental_details(cfg, mock_http):
    mock_http["handler"] = handler
    add_source(cfg, "hinsdale", "buildium", base_url=BASE)
    j = runner.run_inline("scrape", source_id="hinsdale", cfg=cfg)
    assert j.status == "succeeded", j.error
    assert j.result["listings"] == 8 and j.result["healthy"]
    assert (j.done, j.total) == (9, 9)
    first_run = len(mock_http["log"])
    assert first_run == 1 + 1 + 8 * 2  # robots + search + detail/images per listing

    mock_http["log"].clear()
    j2 = runner.run_inline("scrape", source_id="hinsdale", cfg=cfg)
    assert j2.result["new_snapshots"] == 0
    assert len(mock_http["log"]) == 2  # robots + search only; details reused
    with session_scope(cfg) as s:
        snap = s.query(ListingSnapshot).join(SourceListing, SourceListing.id == ListingSnapshot.source_listing_id) \
            .filter(SourceListing.external_id == "110169").one()
        assert "Balcony, deck, patio" in snap.amenities and snap.photos


def test_disabled_public_listings_fail_loudly(cfg, mock_http):
    mock_http["handler"] = lambda req: (
        httpx.Response(302, headers={"location": f"{BASE}/Resident/apps/portal/login"})
        if "apartmentsearch" in req.url.path.lower() else httpx.Response(200, text="<html>login</html>"))
    add_source(cfg, "hinsdale", "buildium", base_url=BASE)
    j = runner.run_inline("scrape", source_id="hinsdale", cfg=cfg)
    assert j.status == "failed" and "disabled" in j.error
