"""Script writer: topic + format -> narration beats with per-beat visual plans."""
from __future__ import annotations

import base64
from pathlib import Path

from . import formats, llm
from .settings import Settings

WORDS_PER_SECOND = 2.6
KINDS = ["auto", "ai_image", "ai_video", "stock", "movie", "source"]

BEAT = llm.obj({
    "narration": {"type": "string"},
    "kind": {"type": "string", "enum": KINDS},
    "image_prompt": {"type": "string"},
    "stock_query": {"type": "string"},
    "movie_title": {"type": "string"},
    "movie_year": {"type": "integer"},
    "source_start": {"type": "number", "default": -1},
    "emphasis": {"type": "boolean"},
})
SCHEMA = llm.obj({
    "title": {"type": "string"},
    "description": {"type": "string"},
    "hashtags": {"type": "array", "items": {"type": "string"}},
    "thumbnail_text": {"type": "string"},
    "character": {"type": "string"},
    "style": {"type": "string"},
    "beats": {"type": "array", "items": BEAT},
})

SYSTEM = """You are the head writer of a viral faceless video channel. Your scripts are read
by an AI narrator over fast-cut visuals, so they live or die on retention.

Narration rules:
- Plain spoken words only: no stage directions, emojis, hashtags or speaker labels.
- One beat = one short sentence or two very short ones (about 2-4 seconds spoken).
- The first beat is the hook and must make scrolling away feel impossible.
- Never say "in this video", "let's dive in", "subscribe" or similar.

Visual rules (each beat gets exactly one visual):
- image_prompt: a self-contained prompt for an image generator describing exactly what the
  narrator is talking about in that beat: subject, action, setting, camera angle, lighting,
  emotion. Keep the main subject centred (it will be cropped to both 9:16 and 16:9).
  Never ask for text, captions or logos in the image. Always fill this in, even for other
  kinds, because it is the fallback.
- If there is a recurring protagonist, describe them once in `character` (age, build, hair,
  clothing) and paste that description into every image_prompt they appear in.
- `style` is the art direction shared by all images.
- stock_query: 2-4 word stock-footage search for the beat (always fill in).
- kind: "auto" unless a specific source is clearly best. Allowed specific kinds: {kinds}.
- emphasis: true for the few most dramatic beats (they get premium treatment).
- movie_title / movie_year: the movie the beat is about, or "" / 0.
- source_start: seconds into the user's footage for this beat, or -1.
"""


def _frames_content(frames: list[tuple[float, Path]]) -> list[dict]:
    content: list[dict] = []
    for t, p in frames:
        content.append({"type": "text", "text": f"Frame at {t:.1f}s:"})
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg",
            "data": base64.standard_b64encode(p.read_bytes()).decode(),
        }})
    return content


def write(s: Settings, fmt_name: str, topic: str, angle: str = "", *, job_id: int | None = None,
          frames: list[tuple[float, Path]] | None = None, source_duration: float | None = None) -> dict:
    fmt = formats.get(fmt_name)
    words = int(s.target_seconds * WORDS_PER_SECOND)
    beats = max(6, round(s.target_seconds / 3.2))

    kinds = ["ai_image"]
    if s.video_provider != "none":
        kinds.append(f"ai_video (at most {s.max_ai_video_clips} beats)")
    if s.visual_mix.get("stock", 0) > 0 and s.secret("pexels_api_key"):
        kinds.append("stock")
    if fmt.get("uses_movies") and s.mediaserver_enabled:
        kinds.append("movie")
    if frames:
        kinds.append("source")

    prompt = f"""Write a script.

Topic: {topic}
{('Angle: ' + angle) if angle else ''}
Format: {fmt['label']} - {fmt.get('description', '')}
Structure:
{fmt['structure'].strip()}
Tone: {fmt['tone']}
Length: about {words} words of narration across about {beats} beats (~{s.target_seconds}s spoken).
Default art style (refine it, keep it consistent): {s.visual_style or fmt['visual_style']}
Recurring protagonist: {'yes' if fmt.get('recurring_character') else 'only if it helps'}
{s.extra_script_notes}

Also return a YouTube/TikTok title (under 70 chars), a 1-2 sentence description, 5-8
hashtags without '#', and 2-5 words of punchy thumbnail_text."""

    content: list[dict] | str = prompt
    if frames:
        content = _frames_content(frames) + [{"type": "text", "text":
            f"{prompt}\n\nThe frames above are from the user's video ({source_duration or 0:.0f}s long)."}]

    script = llm.structured(s, SYSTEM.format(kinds=", ".join(kinds)), content, SCHEMA,
                            job_id=job_id, what="script")
    script["beats"] = [b for b in script["beats"] if b["narration"].strip()]
    if not script["beats"]:
        raise llm.LLMError("script came back with no narration")
    script["format"] = fmt_name
    return script
