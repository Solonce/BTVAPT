"""Timeline -> finished video, using ffmpeg.

Each shot becomes a short silent segment at the output size (stills get eased
pan/zoom motion, clips get trimmed and cover-cropped); segments are
concatenated, then captions, narration and ducked music are mixed in.
"""
from __future__ import annotations

import random
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import captions, media
from .settings import Settings

MOTIONS = ["zoom_in", "pan_right", "zoom_out", "pan_left", "pan_down", "pan_up"]


def sizes(s: Settings) -> dict[str, tuple[int, int]]:
    short = s.resolution - s.resolution % 2
    long_ = int(short * 16 / 9) // 2 * 2
    out = {}
    if s.vertical:
        out["vertical"] = (short, long_)
    if s.horizontal:
        out["horizontal"] = (long_, short)
    return out


def pick_motions(n: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    out, prev = [], None
    for _ in range(n):
        m = rng.choice([x for x in MOTIONS if x != prev])
        out.append(m)
        prev = m
    return out


def _motion_filter(motion: str, frames: int, w: int, h: int, fps: int, intensity: float, punch: bool) -> str:
    n1 = max(1, frames - 1)
    e = f"((on/{n1})*(on/{n1})*(3-2*on/{n1}))"   # smoothstep easing
    zoom_amt = 0.16 * intensity * (1.7 if punch else 1.0)
    pan_zoom = 1 + 0.10 + 0.05 * intensity
    cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    if intensity <= 0:
        z, x, y = "1", cx, cy
    elif motion == "zoom_in":
        z, x, y = f"1+{zoom_amt:.3f}*{e}", cx, cy
    elif motion == "zoom_out":
        z, x, y = f"1+{zoom_amt:.3f}*(1-{e})", cx, cy
    elif motion == "pan_right":
        z, x, y = f"{pan_zoom:.3f}", f"(iw-iw/zoom)*{e}", cy
    elif motion == "pan_left":
        z, x, y = f"{pan_zoom:.3f}", f"(iw-iw/zoom)*(1-{e})", cy
    elif motion == "pan_down":
        z, x, y = f"{pan_zoom:.3f}", cx, f"(ih-ih/zoom)*{e}"
    else:  # pan_up
        z, x, y = f"{pan_zoom:.3f}", cx, f"(ih-ih/zoom)*(1-{e})"
    up_w, up_h = w * 3, h * 3   # oversample so slow motion doesn't jitter
    return (f"scale={up_w}:{up_h}:force_original_aspect_ratio=increase:flags=lanczos,crop={up_w}:{up_h},"
            f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={w}x{h}:fps={fps},"
            f"unsharp=5:5:0.6:5:5:0,setsar=1,format=yuv420p")


ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p"]


def segment(shot: dict, w: int, h: int, s: Settings, dest: Path) -> Path:
    dur = shot["duration"]
    frames = max(2, round(dur * s.fps))
    asset = shot["asset"]
    if asset["type"] == "image":
        vf = _motion_filter(shot["motion"], frames, w, h, s.fps, s.motion_intensity, shot.get("emphasis", False))
        media.run(["-i", asset["path"], "-vf", vf, "-frames:v", str(frames), "-r", str(s.fps), *ENC, str(dest)])
    else:
        offset = shot.get("offset", 0.0)
        loop = ["-stream_loop", "-1"] if asset.get("duration", 0) and offset + dur > asset["duration"] else []
        if loop:
            offset = 0.0
        vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,"
              f"fps={s.fps},format=yuv420p")
        media.run([*loop, "-ss", f"{offset:.3f}", "-i", asset["path"], "-t", f"{dur:.3f}", "-an",
                   "-vf", vf, "-frames:v", str(frames), "-r", str(s.fps), *ENC, str(dest)])
    return dest


def concat(parts: list[Path], dest: Path) -> Path:
    lst = dest.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.name}'\n" for p in parts))
    media.run(["-f", "concat", "-safe", "0", "-i", lst.name, "-c", "copy", dest.name], cwd=dest.parent)
    return dest


def final(s: Settings, video: Path, narration: Path, words: list[dict], total: float,
          w: int, h: int, dest: Path, music: Path | None) -> Path:
    workdir = video.parent
    args = ["-i", video.name, "-i", str(narration.resolve())]
    if music:
        args += ["-stream_loop", "-1", "-i", str(music.resolve())]
    filters = []
    vout = "0:v"
    if s.captions and words:
        ass = captions.build(s, words, w, h, workdir / f"captions_{w}x{h}.ass")
        filters.append(f"[0:v]ass={ass.name}[v]")
        vout = "[v]"
    if music:
        filters.append(
            f"[1:a]apad,asplit=2[n1][n2];[2:a]volume={s.music_volume:.3f}[m];"
            f"[m][n1]sidechaincompress=threshold=0.02:ratio=6:attack=15:release=350[duck];"
            f"[n2][duck]amix=inputs=2:duration=first:normalize=0[a]")
        aout = "[a]"
    else:
        filters.append("[1:a]apad[a]")
        aout = "[a]"
    args += ["-filter_complex", ";".join(filters), "-map", vout, "-map", aout,
             "-t", f"{total:.3f}", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
             str(dest.resolve())]
    media.run(args, cwd=workdir)
    return dest


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        path = subprocess.run(["fc-match", "-f", "%{file}", "sans:bold"], capture_output=True,
                              text=True, timeout=10).stdout.strip()
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default(size)


def thumbnail(image: Path, text: str, w: int, h: int, dest: Path) -> Path:
    img = Image.open(image).convert("RGB")
    scale = max(w / img.width, h / img.height)
    img = img.resize((int(img.width * scale) + 1, int(img.height * scale) + 1))
    left, top = (img.width - w) // 2, (img.height - h) // 2
    img = img.crop((left, top, left + w, top + h))
    d = ImageDraw.Draw(img)
    text = text.upper().strip()
    if text:
        size = int(min(w, h) * 0.13)
        font = _font(size)
        words, lines, cur = text.split(), [], ""
        for word in words:
            trial = (cur + " " + word).strip()
            if d.textlength(trial, font=font) > w * 0.9 and cur:
                lines.append(cur)
                cur = word
            else:
                cur = trial
        lines.append(cur)
        y = int(h * (0.62 if h > w else 0.55))
        for line in lines:
            tw = d.textlength(line, font=font)
            d.text(((w - tw) / 2, y), line, font=font, fill=(255, 225, 77),
                   stroke_width=max(3, size // 10), stroke_fill=(0, 0, 0))
            y += int(size * 1.1)
    img.save(dest, quality=92)
    return dest
