"""Clips from your own media server via a vidsrc-style embed URL.

The embed page is fetched and the real stream (mp4/webm/mkv/m3u8) is pulled out
of it: a direct video response, a <video>/<source> tag, a "file": "..." value,
or one level of nested <iframe>. Then a short clip is cut with ffmpeg.

URL pattern placeholders: {tmdb} (needs a TMDB key), {imdb}, {title}, {year}.
"""
from __future__ import annotations

import random
import re
import urllib.parse
from pathlib import Path

from PIL import Image, ImageStat

from .. import media, net
from . import Asset, Request

MEDIA_RE = re.compile(r"""["'(]([^"'()\s]+?\.(?:m3u8|mp4|webm|mkv|mov)(?:\?[^"'()\s]*)?)["')]""", re.I)
FILE_RE = re.compile(r"""["']?(?:file|src|source|url)["']?\s*[:=]\s*["']([^"']+)["']""", re.I)
IFRAME_RE = re.compile(r"""<iframe[^>]+src=["']([^"']+)["']""", re.I)

_resolved: dict[str, tuple[str, float]] = {}
_used: dict[str, list[float]] = {}


def _tmdb_id(s, title: str, year: int) -> str | None:
    key = s.secret("tmdb_api_key")
    if not key:
        return None
    params = {"query": title, "api_key": key}
    if year:
        params["year"] = year
    with net.client(20) as c:
        r = c.get("https://api.themoviedb.org/3/search/movie", params=params)
        r.raise_for_status()
        res = r.json().get("results", [])
    return str(res[0]["id"]) if res else None


def _embed_url(s, beat: dict) -> str:
    title, year = beat.get("movie_title", ""), beat.get("movie_year") or 0
    path = s.mediaserver_movie_path
    values = {"title": urllib.parse.quote(title), "year": year, "imdb": beat.get("imdb", "")}
    if "{tmdb}" in path:
        tid = _tmdb_id(s, title, year)
        if not tid:
            raise RuntimeError(f"couldn't resolve TMDB id for {title!r} (set a TMDB key)")
        values["tmdb"] = tid
    return s.mediaserver_base.rstrip("/") + "/" + path.lstrip("/").format(**values)


def _is_media(url: str, client) -> bool:
    try:
        with client.stream("GET", url) as r:
            ct = r.headers.get("content-type", "")
            return r.status_code < 400 and ("video" in ct or "mpegurl" in ct or "octet-stream" in ct)
    except Exception:
        return False


def resolve_stream(url: str, depth: int = 0) -> str:
    """Follow an embed page to the actual media URL."""
    with net.client(30) as c:
        with c.stream("GET", url) as r:
            r.raise_for_status()
            ct = r.headers.get("content-type", "")
            if "video" in ct or "mpegurl" in ct:
                return str(r.url)
            html = r.read().decode("utf-8", "ignore")
            base = str(r.url)
        candidates = [m for m in MEDIA_RE.findall(html)]
        candidates += [m for m in FILE_RE.findall(html) if not m.startswith(("javascript:", "#"))]
        seen = set()
        for cand in candidates:
            full = urllib.parse.urljoin(base, cand.replace("\\/", "/"))
            if full in seen:
                continue
            seen.add(full)
            if re.search(r"\.(m3u8|mp4|webm|mkv|mov)(\?|$)", full, re.I) or _is_media(full, c):
                return full
    if depth < 2:
        for frame_src in IFRAME_RE.findall(html):
            try:
                return resolve_stream(urllib.parse.urljoin(base, frame_src), depth + 1)
            except Exception:
                continue
    raise RuntimeError(f"no playable stream found at {url}")


def _bright_enough(stream: str, t: float, tmp: Path) -> bool:
    try:
        media.frame(stream, t, tmp, width=160)
        return ImageStat.Stat(Image.open(tmp).convert("L")).mean[0] > 35
    except Exception:
        return False


class MediaServer:
    name = "mediaserver"
    paid = False

    def available(self, s, req: Request) -> bool:
        return s.mediaserver_enabled and bool(req.beat.get("movie_title"))

    def estimate(self, s, req) -> float:
        return 0.0

    def fetch(self, s, req: Request) -> Asset:
        key = f"{req.beat['movie_title']}|{req.beat.get('movie_year')}"
        if key not in _resolved:
            stream = resolve_stream(_embed_url(s, req.beat))
            _resolved[key] = (stream, media.duration(stream))
        stream, total = _resolved[key]
        need = max(req.duration, s.clip_seconds_min) + 0.5
        used = _used.setdefault(key, [])
        tmp = req.workdir / f"probe{req.index:03d}.jpg"
        t = total * 0.3
        for _ in range(6):
            t = random.uniform(total * 0.08, max(total * 0.08, total * 0.85 - need))
            if all(abs(t - u) > 20 for u in used) and _bright_enough(stream, t + need / 2, tmp):
                break
        used.append(t)
        dest = req.workdir / f"beat{req.index:03d}_movie.mp4"
        media.run(["-ss", f"{t:.2f}", "-i", stream, "-t", f"{need:.2f}", "-an",
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", str(dest)], timeout=600)
        return Asset(dest, "video", duration=media.duration(dest), source=self.name,
                     meta={"movie": req.beat["movie_title"], "at": round(t, 1)})
