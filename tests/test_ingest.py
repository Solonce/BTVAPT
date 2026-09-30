from btv import settings
from btv.db import session_scope
from btv.jobs import runner
from btv.models import ListingSnapshot, RawFetch, Source, SourceListing, StatusEvent

from .conftest import listing


def scrape(cfg):
    j = runner.run_inline("scrape", source_id="demo", cfg=cfg)
    assert j.status == "succeeded", j.error
    return j.result


def statuses(cfg):
    with session_scope(cfg) as s:
        return {sl.external_id: (sl.status, sl.consecutive_misses) for sl in s.query(SourceListing)}


def test_snapshots_only_on_change_and_raw_archived(cfg, file_source):
    file_source([listing("a"), listing("b")])
    assert scrape(cfg)["new_snapshots"] == 2
    assert scrape(cfg)["new_snapshots"] == 0
    file_source([listing("a", rent=1600), listing("b")])
    assert scrape(cfg)["new_snapshots"] == 1
    with session_scope(cfg) as s:
        rents = [x.rent for x in s.query(ListingSnapshot).order_by(ListingSnapshot.id)]
        assert rents == [1500, 1500, 1600]
        assert all(x.url.startswith("https://example.com/") for x in s.query(ListingSnapshot))
        assert s.query(RawFetch).count() == 3


def test_gone_after_consecutive_healthy_misses_then_relisted(cfg, file_source):
    file_source([listing("a"), listing("b")])
    scrape(cfg)
    file_source([listing("a")])
    scrape(cfg)
    scrape(cfg)
    assert statuses(cfg)["b"] == ("available", 2)
    scrape(cfg)
    assert statuses(cfg)["b"] == ("gone", 3)
    file_source([listing("a"), listing("b")])
    scrape(cfg)
    assert statuses(cfg)["b"] == ("relisted", 0)
    with session_scope(cfg) as s:
        sl = s.query(SourceListing).filter_by(external_id="b").one()
        trail = [e.to_status for e in s.query(StatusEvent).filter_by(source_listing_id=sl.id).order_by(StatusEvent.id)]
    assert trail == ["available", "gone", "relisted"]


def test_unhealthy_run_does_not_count_misses(cfg, file_source):
    with session_scope(cfg) as s:
        s.get(Source, "demo").expected_min = 1
        settings.set_value(s, "gone_after_misses", 1)
    file_source([listing("a")])
    scrape(cfg)
    file_source([])  # e.g. site served an empty shell
    result = scrape(cfg)
    assert result["healthy"] is False
    assert statuses(cfg)["a"] == ("available", 0)
    with session_scope(cfg) as s:
        assert s.get(Source, "demo").last_status == "unhealthy"


def test_sudden_drop_is_unhealthy(cfg, file_source):
    file_source([listing(str(i)) for i in range(10)])
    scrape(cfg)
    file_source([listing("0"), listing("1")])
    result = scrape(cfg)
    assert result["healthy"] is False
    assert "sudden drop" in result["problems"][0]


def test_pending_status_from_source(cfg, file_source):
    file_source([listing("a", status="pending")])
    scrape(cfg)
    assert statuses(cfg)["a"][0] == "pending"
    file_source([listing("a")])
    scrape(cfg)
    assert statuses(cfg)["a"][0] == "available"


def test_listing_without_url_fails_run(cfg, file_source):
    file_source([listing("a", url="")])
    j = runner.run_inline("scrape", source_id="demo", cfg=cfg)
    assert j.status == "failed" and "no URL" in j.error
    with session_scope(cfg) as s:
        src = s.get(Source, "demo")
        assert (src.last_status, src.consecutive_failures) == ("failed", 1)


def test_disabled_source_is_skipped(cfg, file_source):
    with session_scope(cfg) as s:
        s.get(Source, "demo").enabled = False
    j = runner.run_inline("scrape", source_id="demo", cfg=cfg)
    assert j.result == {"skipped": "disabled"}
