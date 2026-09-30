"""``file`` platform: ingest listings from a local JSON file.

Used for tests and demos, and as the landing spot for manual paste-in
(e.g. Facebook posts) until a dedicated importer exists. The file holds a
list of objects with ``ParsedListing`` field names.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

from btv.sources.base import Adapter, ParsedListing, parse_date, register

_FIELDS = set(ParsedListing.__dataclass_fields__) - {"raw_fetch_id"}


@register
class FileAdapter(Adapter):
    platform = "file"
    parser_version = "1"

    def run(self, ctx) -> Iterator[ParsedListing]:
        path = Path(self.config["path"])
        ctx.step(f"reading {path}")
        body = path.read_bytes()
        fetch_id = self.fetcher.record(path.resolve().as_uri(), body, "application/json", via="manual")
        import json

        items = json.loads(body)
        self.complete_feed = bool(self.config.get("complete_feed", True))
        ctx.set_total(len(items))
        for item in items:
            data = {k: v for k, v in item.items() if k in _FIELDS}
            data["extra"] = {k: v for k, v in item.items() if k not in _FIELDS}
            data["avail_structured_date"] = parse_date(data.get("avail_structured_date"))
            yield ParsedListing(raw_fetch_id=fetch_id, **data)
            ctx.advance(step=f"parsed {data.get('external_id')}")
