"""One job end to end: script -> voice -> footage -> render -> output folder."""
from __future__ import annotations

import json
import logging
import math
import random
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import db, media, render, scriptgen, tts
from .settings import DATA, Settings
from .visuals import SOURCES, Asset, Request

log = logging.getLogger(__name__)


class Cancelled(Exception):
    pass


class Paused(Exception):
    pass


class Job:
    def __init__(self, job_id: int):
        self.id = job_id
        row = db.job(job_id)
        self.row = row
        self.s = Settings(**json.loads(row["settings_json"]))
        self.dir = DATA / "jobs" / str(job_id)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.source_video = Path(row["source_video"]) if row["source_video"] else None

    def progress(self, step: str, pct: float) -> None:
        if db.job(self.id)["status"] == "cancelled":
            raise Cancelled()
        db.update_job(self.id, step=step, progress=round(pct, 3))

    def log(self, msg: str) -> None:
        log.info("job %s: %s", self.id, msg)
        db.log_job(self.id, msg)

    def cost(self) -> float:
        return db.job(self.id)["cost_usd"]


# --- planning ---------------------------------------------------------------

def beat_times(beats: list[dict], words: list[dict], audio_dur: float) -> list[tuple[float, float]]:
    offsets, pos = [], 0
    for b in beats:
        offsets.append(pos)
        pos += len(b["narration"].strip()) + 1
    starts = []
    for i, off in enumerate(offsets):
        w = next((w for w in words if w["char"] >= off), None)
        t = w["start"] if w else (starts[-1] if starts else 0.0)
        starts.append(0.0 if i == 0 else max(t, starts[-1] + 0.3))
    ends = starts[1:] + [audio_dur + 0.4]
    return [(a, max(b, a + 0.3)) for a, b in zip(starts, ends)]


def choose_kind(job: Job, beat: dict, budget_left: dict, rng: random.Random) -> list[str]:
    """Ordered list of kinds to try for a beat; the router behind 'fluidity'."""
    s, req_kind = job.s, beat.get("kind", "auto")
    order: list[str] = []
    if job.source_video and req_kind in ("source", "auto"):
        order.append("source")
    if beat.get("movie_title") and s.mediaserver_enabled:
        order.append("movie")
    wants_video = req_kind == "ai_video" or (beat.get("emphasis") and req_kind == "auto")
    if wants_video and s.video_provider != "none" and budget_left["video_clips"] > 0:
        order.append("ai_video")
    if req_kind in ("stock", "ai_image"):
        order.append(req_kind)
    weights = {k: v for k, v in s.visual_mix.items() if v > 0}
    while weights:
        k = rng.choices(list(weights), list(weights.values()))[0]
        order.append(k)
        weights.pop(k)
    order += ["ai_image", "card"]
    return list(dict.fromkeys(order))


def fetch_asset(job: Job, req: Request, kinds: list[str], budget_left: dict) -> Asset:
    errors = []
    for kind in kinds:
        for src in SOURCES.get(kind, []):
            try:
                if not src.available(job.s, req):
                    continue
                if src.paid:
                    est = src.estimate(job.s, req)
                    if job.cost() + est > job.s.per_video_max_usd or \
                            db.month_spend() + est > job.s.monthly_budget_usd:
                        errors.append(f"{src.name}: over budget")
                        continue
                asset = src.fetch(job.s, req)
                if kind == "ai_video":
                    budget_left["video_clips"] -= 1
                return asset
            except Exception as e:
                errors.append(f"{src.name}: {e}")
    raise RuntimeError("; ".join(errors) or "no footage source available")


# --- steps ------------------------------------------------------------------

def step_script(job: Job) -> dict:
    if job.row["script_json"]:
        return json.loads(job.row["script_json"])
    job.progress("Writing script", 0.03)
    frames, src_dur = None, None
    if job.source_video:
        src_dur = media.duration(job.source_video)
        n = max(6, min(24, int(src_dur / 4)))
        frames = []
        for i in range(n):
            t = src_dur * (i + 0.5) / n
            frames.append((t, media.frame(job.source_video, t, job.dir / f"src_frame{i:02d}.jpg")))
        job.log(f"Sampled {n} frames from source video ({src_dur:.0f}s)")
    topic = db.row("SELECT * FROM topics WHERE id=?", job.row["topic_id"]) if job.row["topic_id"] else None
    script = scriptgen.write(job.s, job.row["format"], job.row["title"], topic["angle"] if topic else "",
                             job_id=job.id, frames=frames, source_duration=src_dur)
    db.update_job(job.id, script_json=script, title=script["title"])
    job.log(f"Script: {script['title']!r}, {len(script['beats'])} beats")
    return script


def step_voice(job: Job, script: dict) -> tuple[Path, list[dict], float]:
    job.progress("Recording narration", 0.18)
    text = " ".join(b["narration"].strip() for b in script["beats"])
    out = job.dir / "narration.mp3"
    words, dur = tts.synthesize(job.s, text, out, job.id)
    (job.dir / "words.json").write_text(json.dumps(words))
    job.log(f"Narration {dur:.1f}s ({job.s.tts_provider}), {len(words)} timed words")
    return out, words, dur


def step_footage(job: Job, script: dict, times: list[tuple[float, float]]) -> list[dict]:
    s = job.s
    rng = random.Random(job.id)
    budget_left = {"video_clips": s.max_ai_video_clips}
    beats = script["beats"]
    plan = [choose_kind(job, b, budget_left, rng) for b in beats]
    assets: dict[tuple[int, int], Asset] = {}
    done = [0]

    # Split long beats into several shots so the picture keeps moving
    shots = []
    for i, (b, (a, z)) in enumerate(zip(beats, times)):
        n = max(1, math.ceil((z - a) / max(1.0, s.max_shot_seconds)))
        for k in range(n):
            shots.append({"beat": i, "sub": k, "start": a + (z - a) * k / n,
                          "duration": (z - a) / n, "emphasis": bool(b.get("emphasis")) and k == 0})

    def get(i: int, variant: int, dur: float) -> None:
        req = Request(beat=beats[i], index=i, duration=dur, script=script, workdir=job.dir,
                      job_id=job.id, source_video=job.source_video, variant=variant)
        try:
            if variant == 0:
                assets[(i, 0)] = fetch_asset(job, req, plan[i], budget_left)
            else:
                assets[(i, variant)] = fetch_asset(job, req, ["ai_image"], budget_left)
        except Cancelled:
            raise
        except Exception as e:
            if variant == 0:
                job.log(f"Beat {i + 1}: footage failed ({e}); using a card")
                assets[(i, 0)] = SOURCES["card"][0].fetch(s, req)
            # extra shots just reuse the beat's main asset with different motion
        done[0] += 1
        job.progress("Gathering footage", 0.25 + 0.4 * done[0] / max(1, total))

    first = [(i, 0, times[i][1] - times[i][0]) for i in range(len(beats))]
    total = len(first)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda a: get(*a), first))

    # Extra shots: alternate stills for image beats, other moments for clips
    extras = [(sh["beat"], sh["sub"], sh["duration"]) for sh in shots
              if sh["sub"] > 0 and assets[(sh["beat"], 0)].type == "image"
              and assets[(sh["beat"], 0)].source != "card"]
    total += len(extras)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda a: get(*a), extras))

    motions = render.pick_motions(len(shots), job.id)
    for n, sh in enumerate(shots):
        base = assets[(sh["beat"], 0)]
        a = assets.get((sh["beat"], sh["sub"]), base)
        offset = a.offset
        if a.type == "video" and sh["sub"] > 0:
            if a.source == "source":
                offset = a.offset + sh["start"] - shots[[x["beat"] for x in shots].index(sh["beat"])]["start"]
            elif a.duration > sh["duration"]:
                offset = rng.uniform(0, a.duration - sh["duration"])
        sh.update(asset={"path": str(a.path), "type": a.type, "duration": a.duration, "source": a.source},
                  offset=offset, motion=motions[n])
    used = {}
    for sh in shots:
        used[sh["asset"]["source"]] = used.get(sh["asset"]["source"], 0) + 1
    job.log(f"{len(shots)} shots from {used}")
    (job.dir / "shots.json").write_text(json.dumps(shots, indent=1))
    return shots


def _music(s: Settings) -> Path | None:
    if not s.music:
        return None
    files = [Path(r["path"]) for r in db.rows("SELECT path FROM library WHERE kind='music'")]
    files += [p for p in (DATA / "music").glob("*") if p.suffix.lower() in (".mp3", ".m4a", ".wav", ".ogg")]
    files = [p for p in files if p.exists()]
    return random.choice(files) if files else None


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "video"


def step_render(job: Job, script: dict, shots: list[dict], narration: Path, words: list[dict],
                audio_dur: float) -> dict:
    s = job.s
    out_dir = s.output_path() / f"{time.strftime('%Y-%m-%d')}_{job.id}_{_slug(script['title'])}"
    out_dir.mkdir(parents=True, exist_ok=True)
    total_len = shots[-1]["start"] + shots[-1]["duration"]
    music = _music(s)
    outputs = {"folder": str(out_dir), "videos": {}, "thumbnails": {}}
    targets = render.sizes(s)
    steps = len(shots) * len(targets)
    k = 0
    for name, (w, h) in targets.items():
        seg_dir = job.dir / f"seg_{name}"
        seg_dir.mkdir(exist_ok=True)
        parts = []
        for i, sh in enumerate(shots):
            parts.append(render.segment(sh, w, h, s, seg_dir / f"s{i:04d}.mp4"))
            k += 1
            job.progress(f"Rendering {name}", 0.66 + 0.28 * k / steps)
        joined = render.concat(parts, seg_dir / "joined.mp4")
        dest = out_dir / f"{name}.mp4"
        render.final(s, joined, narration, words, total_len, w, h, dest, music)
        outputs["videos"][name] = str(dest)
        job.log(f"Rendered {name} {w}x{h}")

    # Thumbnails from the strongest still
    job.progress("Thumbnails & metadata", 0.96)
    still = None
    for sh in sorted(shots, key=lambda x: not x.get("emphasis")):
        if sh["asset"]["type"] == "image" and sh["asset"]["source"] != "card":
            still = Path(sh["asset"]["path"])
            break
    if still is None:
        still = media.frame(outputs["videos"][next(iter(outputs["videos"]))], total_len * 0.3,
                            job.dir / "thumb_src.jpg", width=1280)
    for name, (w, h) in {"vertical": (1080, 1920), "horizontal": (1280, 720)}.items():
        if name in targets:
            p = render.thumbnail(still, script.get("thumbnail_text", ""), w, h, out_dir / f"thumbnail_{name}.jpg")
            outputs["thumbnails"][name] = str(p)

    meta = {
        "title": script["title"], "description": script["description"],
        "hashtags": script["hashtags"], "format": script.get("format"),
        "duration_seconds": round(total_len, 1), "cost_usd": round(job.cost(), 4),
    }
    (out_dir / "metadata.json").write_text(json.dumps(meta, indent=2))
    (out_dir / "upload.txt").write_text(
        f"{script['title']}\n\n{script['description']}\n\n" + " ".join("#" + h for h in script["hashtags"]) + "\n")
    (out_dir / "script.txt").write_text("\n\n".join(b["narration"] for b in script["beats"]) + "\n")
    shutil.copy(job.dir / "shots.json", out_dir / "shots.json")
    for name in targets:  # intermediate segments are large and no longer needed
        shutil.rmtree(job.dir / f"seg_{name}", ignore_errors=True)
    return outputs


def run(job_id: int) -> None:
    job = Job(job_id)
    db.update_job(job_id, status="running", error=None)
    try:
        if db.month_spend() >= job.s.monthly_budget_usd:
            raise RuntimeError(f"Monthly budget of ${job.s.monthly_budget_usd:.2f} reached")
        script = step_script(job)
        if job.s.require_approval and not job.row["script_json"]:
            db.update_job(job_id, status="awaiting_approval", step="Waiting for script approval", progress=0.15)
            job.log("Paused for approval")
            return
        narration, words, audio_dur = step_voice(job, script)
        times = beat_times(script["beats"], words, audio_dur)
        shots = step_footage(job, script, times)
        outputs = step_render(job, script, shots, narration, words, audio_dur)
        db.update_job(job_id, status="done", step="Done", progress=1.0, output_json=outputs)
        job.log(f"Done -> {outputs['folder']} (cost ${job.cost():.3f})")
    except Cancelled:
        job.log("Cancelled")
    except Exception as e:
        log.exception("job %s failed", job_id)
        db.update_job(job_id, status="failed", error=str(e)[:2000])
        job.log(f"FAILED: {e}")
