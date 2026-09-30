import json

import pytest

from btv import config as config_mod
from btv.db import init_db, session_scope
from btv.models import Source


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("BTV_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("BTV_PER_HOST_INTERVAL", "0")
    config_mod.get_config.cache_clear()
    c = config_mod.get_config()
    init_db(c)
    yield c
    config_mod.get_config.cache_clear()


@pytest.fixture
def file_source(cfg, tmp_path):
    """A 'file' platform source plus a helper to rewrite its listings."""
    path = tmp_path / "listings.json"

    def write(items):
        path.write_text(json.dumps(items))

    write([])
    with session_scope(cfg) as s:
        s.add(Source(id="demo", name="Demo", platform="file", config={"path": str(path)},
                     interval_minutes=60, expected_min=0))
    return write


def listing(ext_id, **kw):
    d = {"external_id": ext_id, "url": f"https://example.com/{ext_id}", "rent": 1500}
    d.update(kw)
    return d
