"""Free stock video from Pexels (free API key)."""
from __future__ import annotations

import random

from .. import media, net
from . import Asset, Request

_used: set[int] = set()


class Pexels:
    name = "pexels"
    paid = False

    def available(self, s, req) -> bool:
        return bool(s.secret("pexels_api_key"))

    def estimate(self, s, req) -> float:
        return 0.0

    def fetch(self, s, req: Request) -> Asset:
        query = req.beat.get("stock_query") or req.beat["narration"][:60]
        orientation = "portrait" if s.vertical and not s.horizontal else "landscape"
        with net.client(30) as c:
            r = c.get("https://api.pexels.com/videos/search",
                      params={"query": query, "orientation": orientation, "per_page": 15, "size": "medium"},
                      headers={"Authorization": s.secret("pexels_api_key")})
            r.raise_for_status()
            videos = [v for v in r.json().get("videos", []) if v["duration"] >= 3 and v["id"] not in _used]
        if not videos:
            raise RuntimeError(f"no stock footage for {query!r}")
        v = random.choice(videos[:6])
        _used.add(v["id"])
        files = [f for f in v["video_files"] if f.get("height") and f.get("file_type") == "video/mp4"]
        target = s.resolution
        f = min(files, key=lambda f: abs(min(f["width"], f["height"]) - target))
        dest = req.workdir / f"beat{req.index:03d}_pexels.mp4"
        net.download(f["link"], dest)
        dur = media.duration(dest)
        offset = random.uniform(0, max(0.0, dur - req.duration - 0.2))
        return Asset(dest, "video", duration=dur, offset=offset, source=self.name,
                     meta={"pexels_id": v["id"], "url": v["url"]})
