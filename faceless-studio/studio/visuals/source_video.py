"""The user's uploaded video for 'narrate my footage' jobs."""
from __future__ import annotations

from .. import media
from . import Asset, Request


class SourceVideo:
    name = "source"
    paid = False

    def available(self, s, req: Request) -> bool:
        return req.source_video is not None and req.source_video.exists()

    def estimate(self, s, req) -> float:
        return 0.0

    def fetch(self, s, req: Request) -> Asset:
        dur = media.duration(req.source_video)
        start = req.beat.get("source_start", -1)
        if start is None or start < 0:
            # Spread un-timed beats across the video in order
            n = max(1, len(req.script["beats"]))
            start = dur * req.index / n
        start = min(max(0.0, start), max(0.0, dur - req.duration))
        return Asset(req.source_video, "video", duration=dur, offset=start, source=self.name)
