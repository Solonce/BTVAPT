"""User-changeable runtime settings stored in the DB.

These never filter ingest; they are applied at view time only.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from btv.models import Setting

DEFAULTS: dict[str, object] = {
    "target_move_in": "2027-06-01",
    # Listings available within +/- this many days of the target show as near misses.
    "near_miss_days": 45,
    # Consecutive healthy-run misses before a manager-site listing is marked gone.
    "gone_after_misses": 3,
}


def _validate(key: str, value: object) -> object:
    if key not in DEFAULTS:
        raise KeyError(f"unknown setting {key!r}; known: {sorted(DEFAULTS)}")
    if key == "target_move_in":
        return date.fromisoformat(str(value)).isoformat()
    if key in ("near_miss_days", "gone_after_misses"):
        v = int(value)  # type: ignore[arg-type]
        if v < 0:
            raise ValueError(f"{key} must be >= 0")
        return v
    return value


def get_all(session: Session) -> dict[str, object]:
    out = dict(DEFAULTS)
    for row in session.query(Setting).all():
        if row.key in out:
            out[row.key] = row.value
    return out


def get(session: Session, key: str) -> object:
    return get_all(session)[key]


def set_value(session: Session, key: str, value: object) -> object:
    value = _validate(key, value)
    row = session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value
    return value


def target_date(session: Session) -> date:
    return date.fromisoformat(str(get(session, "target_move_in")))
