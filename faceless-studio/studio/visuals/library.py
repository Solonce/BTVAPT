"""Your own footage library (uploaded in the UI), matched to beats by tags."""
from __future__ import annotations

import random
import re
from pathlib import Path

from .. import db
from . import Asset, Request


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2}


class Library:
    name = "library"
    paid = False

    def _items(self):
        return [r for r in db.rows("SELECT * FROM library WHERE kind IN ('video','image')") if Path(r["path"]).exists()]

    def available(self, s, req) -> bool:
        return bool(self._items())

    def estimate(self, s, req) -> float:
        return 0.0

    def fetch(self, s, req: Request) -> Asset:
        want = _words(req.beat.get("stock_query", "") + " " + req.beat["narration"])
        scored = []
        for it in self._items():
            tags = _words(it["tags"] + " " + Path(it["path"]).stem.replace("_", " "))
            scored.append((len(want & tags) + random.random() * 0.5, it))
        scored.sort(key=lambda x: -x[0])
        it = scored[0][1]
        p = Path(it["path"])
        if it["kind"] == "image":
            return Asset(p, "image", source=self.name)
        dur = it["duration"] or 0
        offset = random.uniform(0, max(0.0, dur - req.duration - 0.2))
        return Asset(p, "video", duration=dur, offset=offset, source=self.name)
