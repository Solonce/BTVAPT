"""Narration: one synthesis pass for the whole script, with word-level timings.

Returns words as {"text", "start", "end", "char"} where `char` is the word's
offset in the full narration text, so words can be mapped back to beats.
"""
from __future__ import annotations

import asyncio
import base64
import os
import re
from pathlib import Path

import aiohttp
import edge_tts

from . import db, media, net
from .settings import Settings

ELEVEN_USD_PER_CHAR = 0.0002  # rough; depends on plan


def _locate(words: list[dict], text: str) -> list[dict]:
    """Attach the character offset of each spoken word in `text`."""
    cursor = 0
    lower = text.lower()
    for w in words:
        token = w["text"].strip().lower()
        idx = lower.find(token, cursor) if token else -1
        if idx == -1 or idx - cursor > 80:
            idx = cursor
        w["char"] = idx
        end = idx + max(1, len(token))
        # Carry trailing punctuation from the script so captions can break on sentences
        while end < len(text) and text[end] in ".,!?;:'\"’”":
            end += 1
        if text[idx:idx + len(token)].lower() == token:
            w["text"] = text[idx:end]
        cursor = end
    return words


async def _edge(s: Settings, text: str, out: Path) -> list[dict]:
    connector = aiohttp.TCPConnector(ssl=net.ssl_context())
    comm = edge_tts.Communicate(text, s.edge_voice, rate=s.edge_rate, boundary="WordBoundary",
                                connector=connector, proxy=os.environ.get("HTTPS_PROXY"))
    words = []
    with open(out, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / 1e7
                words.append({"text": chunk["text"], "start": start, "end": start + chunk["duration"] / 1e7})
    return words


def _elevenlabs(s: Settings, text: str, out: Path, job_id: int | None) -> list[dict]:
    key = s.secret("elevenlabs_api_key")
    if not key:
        raise RuntimeError("ElevenLabs selected but no API key set")
    with net.client(300) as c:
        r = c.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{s.elevenlabs_voice_id}/with-timestamps",
            headers={"xi-api-key": key},
            json={"text": text, "model_id": s.elevenlabs_model},
        )
        r.raise_for_status()
        data = r.json()
    out.write_bytes(base64.b64decode(data["audio_base64"]))
    db.record_spend(job_id, "elevenlabs", len(text) * ELEVEN_USD_PER_CHAR, f"{len(text)} chars")
    al = data["alignment"]
    chars, starts, ends = al["characters"], al["character_start_times_seconds"], al["character_end_times_seconds"]
    words, cur = [], None
    for i, ch in enumerate(chars):
        if ch.isspace():
            if cur:
                words.append(cur)
                cur = None
            continue
        if cur is None:
            cur = {"text": "", "start": starts[i], "end": ends[i], "char": i}
        cur["text"] += ch
        cur["end"] = ends[i]
    if cur:
        words.append(cur)
    return words


def _even(text: str, duration: float) -> list[dict]:
    """Fallback timing: spread words across the audio by character count."""
    toks = [(m.group(), m.start()) for m in re.finditer(r"\S+", text)]
    total = sum(len(t) + 1 for t, _ in toks) or 1
    t, words = 0.0, []
    for tok, pos in toks:
        d = duration * (len(tok) + 1) / total
        words.append({"text": tok, "start": t, "end": t + d, "char": pos})
        t += d
    return words


def tighten(path: Path, words: list[dict], max_gap: float) -> list[dict]:
    """Shorten silences between words to at most `max_gap` seconds, shifting word timings.

    Cuts land in the middle of silence, so speech is untouched; this removes the
    dead air TTS engines leave after every full stop.
    """
    if max_gap <= 0 or len(words) < 2:
        return words
    cuts = []
    for a, b in zip(words, words[1:]):
        if b["start"] - a["end"] > max_gap:
            cuts.append((a["end"] + max_gap / 2, b["start"] - max_gap / 2))
    if not cuts:
        return words
    total = media.duration(path)
    keep, t = [], 0.0
    for cs, ce in cuts:
        keep.append((t, cs))
        t = ce
    keep.append((t, total))
    parts = "".join(f"[0:a]atrim={a:.3f}:{b:.3f},asetpts=PTS-STARTPTS[k{i}];" for i, (a, b) in enumerate(keep))
    graph = parts + "".join(f"[k{i}]" for i in range(len(keep))) + f"concat=n={len(keep)}:v=0:a=1[o]"
    tmp = path.with_name(path.stem + "_tight" + path.suffix)
    media.run(["-i", str(path), "-filter_complex", graph, "-map", "[o]", "-c:a", "libmp3lame", "-q:a", "2", str(tmp)])
    tmp.replace(path)
    out = []
    for w in words:
        shift = sum(ce - cs for cs, ce in cuts if ce <= w["start"] + 1e-6)
        out.append({**w, "start": w["start"] - shift, "end": w["end"] - shift})
    return out


def synthesize(s: Settings, text: str, out: Path, job_id: int | None = None) -> tuple[list[dict], float]:
    if s.tts_provider == "elevenlabs":
        words = _elevenlabs(s, text, out, job_id)
    else:
        words = _locate(asyncio.run(_edge(s, text, out)), text)
    # Strip punctuation-only tokens some engines emit
    words = [w for w in words if re.search(r"\w", w["text"])]
    if words:
        words = tighten(out, words, s.max_pause)
    duration = media.duration(out)
    if not words:
        words = _even(text, duration)
    return words, duration
