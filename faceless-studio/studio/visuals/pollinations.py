"""Free AI images via pollinations.ai (no key). Quality is decent, speed varies."""
from __future__ import annotations

import random
import time
import urllib.parse

from PIL import Image

from .. import net
from . import Asset, Request


class Pollinations:
    name = "pollinations"
    paid = False

    def available(self, s, req: Request) -> bool:
        return True  # always available as the fallback behind fal

    def estimate(self, s, req) -> float:
        return 0.0

    def fetch(self, s, req: Request) -> Asset:
        dest = req.workdir / f"beat{req.index:03d}_v{req.variant}_poll.jpg"
        q = urllib.parse.quote(req.prompt()[:1500], safe="")
        # One seed per video keeps faces/style closer between shots
        seed = (req.job_id or 0) * 1000 + req.variant
        url = (f"https://image.pollinations.ai/prompt/{q}?width=1280&height=1280&nologo=true"
               f"&model=flux&seed={seed}")
        last = None
        for attempt in range(5):
            try:
                net.download(url, dest, timeout=180)
                _crop_watermark(dest)
                return Asset(dest, "image", source=self.name)
            except Exception as e:  # the free service throws intermittent 5xx under load
                last = e
                time.sleep(4 * (attempt + 1))
                if attempt >= 1:
                    url = url.rsplit("&seed=", 1)[0] + f"&seed={random.randint(1, 10**9)}"
        raise RuntimeError(f"pollinations failed: {last}")


def _crop_watermark(path) -> None:
    """The free tier stamps a logo in the bottom-right corner; trim the bottom strip."""
    with Image.open(path) as im:
        w, h = im.size
        im.convert("RGB").crop((0, 0, w, int(h * 0.93))).save(path, quality=95)
