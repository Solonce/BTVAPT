"""Script writer: topic + format -> narration beats with per-beat visual plans."""
from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

from . import formats, llm
from .settings import Settings

log = logging.getLogger(__name__)

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
- It must sound like one continuous story told by a gripping storyteller, not a list of
  captions. Use flowing sentences that carry momentum from one moment into the next with
  connecting words (and, but, so, until, which is when, by the time...). Avoid stacks of
  two- or three-word fragments; a rare punchy fragment for impact is fine.
- Plain spoken words only: no stage directions, emojis, hashtags or speaker labels.
- Beats are for the editor, not the narrator: a beat is the stretch of narration one image
  covers (about 2-4 seconds, roughly 6-12 words). Split long sentences across several beats
  at natural phrase breaks, so a beat can end mid-sentence (on a comma or a conjunction)
  and the next beat continues it.
- Time stamps like "Hour 6" should be woven into sentences ("By hour six, ..."), not read
  as standalone labels.
- The first beat is the hook and must make scrolling away feel impossible.
- Never say "in this video", "let's dive in", "subscribe" or similar.

Visual rules (each beat gets exactly one visual):
- image_prompt: a self-contained prompt for an image generator that SHOWS exactly what the
  narrator is saying in that beat. Lead with the concrete subject or action the line is
  about (the knife, the grave, the trophies, the footprints), then setting, then shot type
  and lighting. If the line mentions an object or event, the object/event is the subject,
  not someone's face. Keep the main subject centred (it will be cropped to 9:16 and 16:9).
  Never ask for text, captions or logos. Always fill this in, even for other kinds,
  because it is the fallback.
- Vary the shots like an editor: wide establishing shots, medium action shots, extreme
  close-ups of objects and details, point-of-view shots, over-the-shoulder. At most a third
  of beats may be a close-up of a face.
- If there is a recurring protagonist, define them in `character`, and when they appear
  in a shot add a SHORT tag at the end of the prompt (5-10 words, e.g. "young woman with a
  black bob and torn tank top"). Leave them out of shots where they aren't the subject.
- The art style is fixed and added to every prompt automatically: do NOT put art-style or
  medium words (photorealistic, 3D render, cinematic, illustration, anime...) in image_prompt.
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
    style = s.style_for(fmt)
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
Length: {words} words of narration in total (hard limit), across about {beats} beats (~{s.target_seconds}s spoken).
Art style of the images (applied automatically; just describe scenes that suit it): {style}
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
    if not frames:  # a revision can't see the user's footage, so leave those scripts alone
        script = _fit_length(s, script, words, job_id)
    script["style"] = style
    script["format"] = fmt_name
    return script


def _count(script: dict) -> int:
    return sum(len(b["narration"].split()) for b in script["beats"])


def _fit_length(s: Settings, script: dict, words: int, job_id: int | None) -> dict:
    """One revision pass when the writer misses the length or writes too few, too-long beats
    (common with open models). Each beat is one image, so beat count is pacing."""
    target_beats = max(6, round(words / 9))
    have, n = _count(script), len(script["beats"])
    if words * 0.75 <= have <= words * 1.25 and n >= target_beats * 0.6:
        return script
    prompt = (f"Revise this script. It has {have} words in {n} beats. Requirements:\n"
              f"- Total narration between {int(words * 0.85)} and {words} words (count carefully); keep "
              f"the hook, the escalation and the ending, and keep the narration flowing.\n"
              f"- About {target_beats} beats of 6-12 words each: split long sentences across several "
              f"beats at natural phrase breaks (a beat may end mid-sentence on a comma).\n"
              f"- Every beat's image_prompt must show exactly what that beat's words describe.\n"
              f"Return the whole script with all fields.\n\n" + json.dumps(script))
    try:
        fixed = llm.structured(s, SYSTEM.format(kinds="ai_image"), prompt, SCHEMA, job_id=job_id,
                               what="script revision")
    except llm.LLMError as e:
        log.warning("script revision failed: %s", e)
        return script
    fixed["beats"] = [b for b in fixed["beats"] if b["narration"].strip()]

    def miss(sc: dict) -> float:
        return abs(_count(sc) - words * 0.92) / words + max(0, target_beats - len(sc["beats"])) / target_beats

    return fixed if fixed["beats"] and miss(fixed) < miss(script) else script
