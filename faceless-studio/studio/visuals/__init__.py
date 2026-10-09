"""Footage sources. Each source is a small plugin registered in SOURCES.

A source turns one beat of the script into a local asset (image or video).
To add a new source: write a module with a class implementing `available`
and `fetch`, and register it below.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ..settings import Settings


@dataclass
class Asset:
    path: Path
    type: str                    # "image" | "video"
    duration: float = 0.0        # videos only
    offset: float = 0.0          # where in the video to start
    source: str = ""
    meta: dict = field(default_factory=dict)


@dataclass
class Request:
    beat: dict
    index: int
    duration: float              # total seconds this beat is on screen
    script: dict
    workdir: Path
    job_id: int | None
    source_video: Path | None = None
    variant: int = 0             # >0 asks for an alternate take of the same beat

    def prompt(self) -> str:
        style = self.script.get("style", "")
        p = self.beat["image_prompt"]
        if self.variant:
            p += ", alternate composition from a different camera angle"
        return f"{p}. Style: {style}. No text, no watermark." if style else p


class Source(Protocol):
    name: str
    paid: bool
    def available(self, s: Settings, req: Request) -> bool: ...
    def estimate(self, s: Settings, req: Request) -> float: ...
    def fetch(self, s: Settings, req: Request) -> Asset: ...


from . import fal, library, mediaserver, pexels, pollinations, source_video, card  # noqa: E402

SOURCES: dict[str, list[Source]] = {
    # kind -> providers tried in order
    "ai_image": [fal.FalImage(), pollinations.Pollinations()],
    "ai_video": [fal.FalVideo()],
    "stock": [pexels.Pexels()],
    "library": [library.Library()],
    "movie": [mediaserver.MediaServer()],
    "source": [source_video.SourceVideo()],
    "card": [card.Card()],
}
