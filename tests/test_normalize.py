from datetime import date

import pytest

from btv.normalize.address import normalize_address
from btv.normalize.amenities import detect_amenities
from btv.normalize.contacts import extract_mentions, is_relay_email, name_similarity, normalize_phone
from btv.normalize.dates import classify, effective_availability, parse_availability

REF = date(2026, 9, 30)


@pytest.mark.parametrize("raw,unit,key,norm_unit", [
    ("46 Lafountain St Apt 1, Burlington VT", None, "46 lafountain st|burlington|vt", "1"),
    ("46 Lafountain Street #1, Burlington, VT 05401", None, "46 lafountain st|burlington|vt", "1"),
    ("122 North Winooski Avenue, Apt. #3, Burlington, VT 05401", "#3", "122 n winooski ave|burlington|vt", "3"),
    ("28 North Main St - 3, Northfield, VT 05663", None, "28 n main st|northfield|vt", "3"),
    ("111 Allen St , Unit 2, Barre, VT 05641", None, "111 allen st|barre|vt", "2"),
    ("25 Elmwood Ave, 17, Burlington, VT 05401", None, "25 elmwood ave|burlington|vt", "17"),
    ("295 Pearl St / 10 Hungerford Terrace, Apt #1A, Burlington, VT", None, "295 pearl st|burlington|vt", "1a"),
    ("201 Saint Paul Street - 3, Burlington, VT", None, "201 st paul st|burlington|vt", "3"),
    ("78 Eastwood Drive, #411, South Burlington, VT 05403", None, "78 eastwood dr|south burlington|vt", "411"),
    ("383 College Street, Suite 2D, Burlington, VT 05401", "Suite 2D", "383 college st|burlington|vt", "2d"),
    ("6 1/2  N. Winooski Ave, Apt #301, Burlington, VT", None, "6 1/2 n winooski ave|burlington|vt", "301"),
])
def test_address_normalization(raw, unit, key, norm_unit):
    a = normalize_address(raw, unit)
    assert (a.building_key, a.unit) == (key, norm_unit)


@pytest.mark.parametrize("text,kind,d", [
    ("Available June 1st, 2027: This bright 2 bedroom", "date", date(2027, 6, 1)),
    ("Available Now!\n\nInitial lease term: Now through 5/24/2027", "now", None),
    ("The field says now but it's available July 22nd.", "date", date(2027, 7, 22)),
    ("Move-in date: 8/15", "date", date(2027, 8, 15)),
    ("Available 6/1/27", "date", date(2027, 6, 1)),
    ("available starting mid-June", "date", date(2027, 6, 15)),
    ("Available late May 2027", "date", date(2027, 5, 31)),
    ("Studio — Available for mid term lease November 1st 2026 - May 1st 2027.", "date", date(2026, 11, 1)),
    ("Availability: flexible", "flexible", None),
    ("Available after current tenant leaves", "vague", None),
])
def test_availability_parsing(text, kind, d):
    r = parse_availability(text, REF)
    assert (r.kind, r.date) == (kind, d)


def test_lease_end_dates_are_ignored():
    assert parse_availability("Lease through 5/31/2027. Great spot.", REF) is None


def test_effective_availability_rules():
    # Description date wins over a stale structured field.
    e = effective_availability(date(2025, 6, 1), "Sunday, June 1, 2025", "date", date(2027, 6, 1), REF, 1.0)
    assert (e.date, e.source) == (date(2027, 6, 1), "text") and "2025-06-01" in e.note
    # Month-only text doesn't override a precise structured date in the same month.
    e = effective_availability(date(2026, 10, 16), "10/16/26", "date", date(2026, 10, 1), REF, 0.8)
    assert (e.date, e.source) == (date(2026, 10, 16), "structured")
    # Past structured date means available now.
    e = effective_availability(date(2026, 9, 1), None, None, None, REF)
    assert e.kind == "now"


def test_classify_near_miss():
    t = date(2027, 6, 1)
    mk = lambda d: effective_availability(d, None, None, None, REF)  # noqa: E731
    assert classify(mk(date(2027, 6, 5)), t, 45)["match"] == "exact"
    assert classify(mk(date(2027, 5, 15)), t, 45) == {"delta_days": -17, "match": "near"}
    assert classify(mk(date(2027, 7, 1)), t, 45) == {"delta_days": 30, "match": "near"}
    assert classify(mk(date(2027, 9, 1)), t, 45)["match"] == "late"


def test_amenities_with_evidence():
    tags = {a["tag"]: a["evidence"] for a in detect_amenities(
        "Rooftop deck with lake views, washer and dryer in unit. Dogs are not allowed.")}
    assert tags["washer/dryer in unit"] == "washer and dryer in unit"
    assert "roof access" in tags and "no pets" in tags and "washer/dryer" not in tags


def test_contacts():
    assert normalize_phone("(802) 731-0170") == normalize_phone("802.731.0170") == "+18027310170"
    assert is_relay_email("hinsprop@email.showmojo.com")
    assert name_similarity("Jane Smith", "J. Smith") >= 0.9
    ms = extract_mentions({"name": "Show Mojo Contact", "phone": "(802) 731-0170"},
                          "Call Jane Smith at 802-555-1234. Contact Real Property Management Sterling. Owner Pays: water.")
    assert ms[0].name is None and ms[0].phone == "+18027310170"
    assert [(m.name, m.phone) for m in ms[1:]] == [("Jane Smith", "+18025551234")]
