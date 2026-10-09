"""Last-resort visual: a moody gradient card, so a render never fails on footage."""
from __future__ import annotations

import random

from PIL import Image, ImageDraw, ImageFilter

from . import Asset, Request


class Card:
    name = "card"
    paid = False

    def available(self, s, req) -> bool:
        return True

    def estimate(self, s, req) -> float:
        return 0.0

    def fetch(self, s, req: Request) -> Asset:
        w = h = 1280
        hue = random.Random(req.index).randint(0, 255)
        a = (hue % 90, (hue * 3) % 60, 40 + hue % 80)
        b = (5, 5, 12)
        img = Image.new("RGB", (w, h), b)
        d = ImageDraw.Draw(img)
        for y in range(h):
            t = y / h
            d.line([(0, y), (w, y)], fill=tuple(int(a[i] * (1 - t) + b[i] * t) for i in range(3)))
        for _ in range(40):
            x, y, r = random.randint(0, w), random.randint(0, h), random.randint(20, 160)
            d.ellipse([x - r, y - r, x + r, y + r], outline=(255, 255, 255, 30))
        img = img.filter(ImageFilter.GaussianBlur(6))
        dest = req.workdir / f"beat{req.index:03d}_v{req.variant}_card.jpg"
        img.save(dest, quality=90)
        return Asset(dest, "image", source=self.name)
