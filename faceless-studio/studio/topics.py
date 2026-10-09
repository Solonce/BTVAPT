"""Self-refilling topic pool, seeded by trend research and past topics."""
from __future__ import annotations

import logging
import time

import httpx

from . import db, formats, llm
from .settings import Settings

log = logging.getLogger(__name__)

TREND_QUERIES = [
    "what happens if", "last 24 hours", "first 100 days", "what your life looks like",
    "day in the life of", "worst movies ranked", "what if you",
]


def youtube_trending_titles(s: Settings, limit: int = 40) -> list[str]:
    key = s.secret("youtube_api_key")
    if not key:
        return []
    after = time.strftime("%Y-%m-%dT00:00:00Z", time.gmtime(time.time() - 30 * 86400))
    titles: list[str] = []
    with httpx.Client(timeout=20) as client:
        for q in TREND_QUERIES:
            try:
                r = client.get("https://www.googleapis.com/youtube/v3/search", params={
                    "part": "snippet", "q": q, "type": "video", "order": "viewCount",
                    "publishedAfter": after, "maxResults": 8, "key": key,
                })
                r.raise_for_status()
                titles += [i["snippet"]["title"] for i in r.json().get("items", [])]
            except httpx.HTTPError as e:
                log.warning("YouTube search failed for %r: %s", q, e)
                break
    return titles[:limit]


def trend_notes(s: Settings) -> str:
    if not s.trend_research:
        return ""
    try:
        return llm.research(
            s,
            "You research what is currently performing well for faceless YouTube Shorts / TikTok channels.",
            "Search the web for faceless video formats and specific video titles that are currently "
            "going viral (timeline stories like 'your last 48 hours in jail', 'first 100 days of a zombie "
            "apocalypse', what-if scenarios, ranked movie lists, history day-in-the-life and similar). "
            "Reply with a compact bullet list: recurring title patterns, specific recent viral titles, "
            "and themes that are trending right now. No preamble.",
            max_searches=4, what="trend research",
        )
    except Exception as e:  # research is optional; never block a refill on it
        log.warning("trend research failed: %s", e)
        return ""


def refill(s: Settings, count: int = 12, only_format: str | None = None) -> int:
    fmts = {k: v for k, v in formats.all_formats().items() if not v.get("uses_source_video")}
    if only_format:
        fmts = {k: v for k, v in fmts.items() if k == only_format} or fmts
    weights = {k: s.format_weights.get(k, 0) for k in fmts}
    if not any(weights.values()):
        weights = {k: 1 for k in fmts}
    wanted = ", ".join(f"{k} (weight {w})" for k, w in weights.items() if w > 0)

    past = [r["title"] for r in db.rows("SELECT title FROM topics ORDER BY id DESC LIMIT 150")]
    yt = youtube_trending_titles(s)
    notes = trend_notes(s)

    fmt_desc = "\n".join(
        f"- {k}: {v['label']} - {v.get('description', '')} Examples: {'; '.join(v['examples'][:4])}"
        for k, v in fmts.items()
    )
    prompt = f"""Generate {count} new video topics for a faceless short-form channel.

Formats (use the key as the format value; distribute roughly by weight: {wanted}):
{fmt_desc}

Trending right now (from research):
{notes or '(no research available)'}

Popular recent YouTube titles:
{chr(10).join('- ' + t for t in yt) or '(none)'}

Already used or queued - do NOT repeat or closely paraphrase these:
{chr(10).join('- ' + t for t in past) or '(none yet)'}

Each title must be a scroll-stopping, curiosity-gap title in the style of the examples. It
is fiction/entertainment, so dark, gross and absurd premises are fine. 'angle' is one
sentence on the twist or hook that makes this one different."""

    schema = llm.obj({"topics": {"type": "array", "items": llm.obj({
        "title": {"type": "string"},
        "format": {"type": "string", "enum": list(fmts)},
        "angle": {"type": "string"},
    })}})
    out = llm.structured(s, "You are the ideas lead for a viral faceless video channel.", prompt,
                         schema, what="topic refill")
    return db.add_topics(out["topics"], source="research" if notes or yt else "generated")


def ensure_pool(s: Settings) -> int:
    n = db.row("SELECT COUNT(*) AS n FROM topics WHERE status='new'")["n"]
    if n < s.topic_pool_min:
        return refill(s, max(12, s.topic_pool_min * 2 - n))
    return 0
