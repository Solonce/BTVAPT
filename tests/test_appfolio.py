import httpx

from btv.jobs import runner
from btv.sources import appfolio

from .conftest import FIXTURES, add_source

BASE = "https://stonebrown.appfolio.com"
LIST = (FIXTURES / "appfolio/stonebrown_listings.html").read_text()
DETAIL = (FIXTURES / "appfolio/stonebrown_detail.html").read_text()


def handler(req):
    if req.url.path == "/listings":
        return httpx.Response(200, text=LIST)
    if req.url.path.startswith("/listings/detail/"):
        return httpx.Response(200, text=DETAIL)
    return httpx.Response(404)


def test_parse_list_pages():
    for name, n in (("stonebrown", 42), ("rpmvt001", 16), ("fpmvt", 7)):
        recs = appfolio.parse_list_page((FIXTURES / f"appfolio/{name}_listings.html").read_text())
        assert len(recs) == n
        assert all(r["lat"] and r["href"].startswith("/listings/detail/") for r in recs)


def test_to_parsed_with_detail():
    rec = appfolio.parse_list_page(LIST)[0]
    pl = appfolio.to_parsed(rec, BASE, appfolio.parse_detail_page(DETAIL))
    assert pl.url == BASE + "/listings/detail/1c5cc2c7-db01-48de-844d-76ec2b943767"
    assert (pl.address_raw, pl.unit_raw) == ("28 North Main St, Northfield, VT 05663", "3")
    assert (pl.rent, pl.beds, pl.baths) == (895, 1.0, 1.0)
    assert pl.avail_structured_raw == "NOW" and pl.avail_structured_date is None
    assert "Heat Included" in pl.amenities
    assert len(pl.photos) > 5 and not pl.description.endswith("...")
    assert pl.extra["deposit"] == 895


def test_helpers():
    assert appfolio.split_address("7 Winter St. - Unit 3, Montpelier, VT 05602") == ("7 Winter St., Montpelier, VT 05602", "3")
    assert appfolio._bed_bath("Studio / 1 ba") == (0.0, 1.0)
    assert str(appfolio.parse_available("10/16/26")) == "2026-10-16"


def test_scrape_end_to_end(cfg, mock_http):
    mock_http["handler"] = handler
    add_source(cfg, "stonebrown", "appfolio", base_url=BASE, robots_override=True)
    j = runner.run_inline("scrape", source_id="stonebrown", cfg=cfg)
    assert j.status == "succeeded", j.error
    assert j.result["listings"] == 42
    # robots_override: no robots.txt request at all
    assert not any(u.endswith("/robots.txt") for u in mock_http["log"])
    mock_http["log"].clear()
    runner.run_inline("scrape", source_id="stonebrown", cfg=cfg)
    assert len(mock_http["log"]) == 1
