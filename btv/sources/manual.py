"""Sources fed from outside the scraper: ``manual`` (bookmarklet / Add page)
and ``email`` (inbox job). Their scheduled "scrape" is a no-op so they show
up in health without ever marking listings gone."""

from __future__ import annotations

from typing import Iterator

from btv.sources.base import Adapter, ParsedListing, register


class _Passive(Adapter):
    complete_feed = False

    def run(self, ctx) -> Iterator[ParsedListing]:
        ctx.set_total(1)
        ctx.advance(step="nothing to fetch; listings arrive via the bookmarklet, Add page or inbox")
        return iter(())


@register
class ManualAdapter(_Passive):
    platform = "manual"


@register
class EmailAdapter(_Passive):
    platform = "email"
