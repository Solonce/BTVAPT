"""FastAPI app: JSON API + the single-page web UI."""
from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db, formats, media, settings, topics, worker

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="Faceless Studio")
_bg = {"refilling": False, "refill_error": ""}


@app.on_event("startup")
def _startup() -> None:
    db.conn()
    worker.start()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")


# Status / settings ---------------------------------------------------------

@app.get("/api/status")
def status() -> dict:
    s = settings.load()
    has = lambda k: bool(s.secret(k))  # noqa: E731
    return {
        "worker": worker.state(),
        "refilling": _bg["refilling"], "refill_error": _bg["refill_error"],
        "month_spend": round(db.month_spend(), 4), "monthly_budget": s.monthly_budget_usd,
        "missing_binaries": media.check(),
        "providers": {
            "writer": has("featherless_api_key") if s.llm_provider == "featherless" else has("anthropic_api_key"),
            "writer_name": "Featherless" if s.llm_provider == "featherless" else "Claude",
            "elevenlabs": has("elevenlabs_api_key"), "fal": has("fal_key"),
            "pexels": has("pexels_api_key"), "youtube": has("youtube_api_key"), "tmdb": has("tmdb_api_key"),
            "mediaserver": s.mediaserver_enabled,
        },
        "counts": {r["status"]: r["n"] for r in db.rows("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status")},
        "topics_new": db.row("SELECT COUNT(*) AS n FROM topics WHERE status='new'")["n"],
        "output_dir": str(s.output_path()),
    }


@app.get("/api/settings")
def get_settings() -> dict:
    return settings.public(settings.load())


@app.put("/api/settings")
def put_settings(values: dict[str, Any] = Body(...)) -> dict:
    s = settings.save(values)
    worker.wake()
    return settings.public(s)


@app.get("/api/presets")
def get_presets() -> dict:
    return {"presets": settings.presets(), "builtin": list(settings.BUILTIN_PRESETS)}


@app.put("/api/presets/{name}")
def put_preset(name: str, overrides: dict[str, Any] = Body(...)) -> dict:
    if name in settings.BUILTIN_PRESETS:
        raise HTTPException(400, "built-in presets can't be overwritten; pick another name")
    settings.save_preset(name, overrides)
    return get_presets()


@app.delete("/api/presets/{name}")
def del_preset(name: str) -> dict:
    settings.delete_preset(name)
    return get_presets()


# Formats -------------------------------------------------------------------

@app.get("/api/formats")
def get_formats() -> dict:
    return formats.all_formats()


@app.put("/api/formats/{name}")
def put_format(name: str, data: dict[str, Any] = Body(...)) -> dict:
    slug = formats.save(name, data)
    return {"saved": slug, "formats": formats.all_formats()}


# Topics --------------------------------------------------------------------

@app.get("/api/topics")
def get_topics(status: str = "new") -> list[dict]:
    return db.rows("SELECT * FROM topics WHERE status=? ORDER BY id DESC LIMIT 300", status)


@app.post("/api/topics")
def add_topic(data: dict[str, Any] = Body(...)) -> dict:
    n = db.add_topics([{"title": data["title"], "format": data.get("format", "timeline_story"),
                        "angle": data.get("angle", "")}], source="manual")
    return {"added": n}


@app.post("/api/topics/refill")
def refill(data: dict[str, Any] = Body(default={})) -> dict:
    if _bg["refilling"]:
        return {"started": False}

    def go():
        _bg.update(refilling=True, refill_error="")
        try:
            topics.refill(settings.load(), int(data.get("count", 12)), data.get("format") or None)
        except Exception as e:
            _bg["refill_error"] = str(e)
        finally:
            _bg["refilling"] = False

    threading.Thread(target=go, daemon=True).start()
    return {"started": True}


@app.patch("/api/topics/{topic_id}")
def patch_topic(topic_id: int, data: dict[str, Any] = Body(...)) -> dict:
    with db.tx() as c:
        c.execute("UPDATE topics SET status=? WHERE id=?", (data["status"], topic_id))
    return {"ok": True}


# Jobs ----------------------------------------------------------------------

def _job_out(j: dict) -> dict:
    j = dict(j)
    for k in ("script_json", "output_json"):
        j[k.replace("_json", "")] = json.loads(j.pop(k)) if j.get(k) else None
    j["settings"] = json.loads(j.pop("settings_json"))
    j.pop("overrides_json", None)
    for k in list(j["settings"]):
        if k in settings.SECRET_FIELDS:
            j["settings"].pop(k)
    return j


@app.get("/api/jobs")
def list_jobs(limit: int = 50) -> list[dict]:
    rows = db.rows("SELECT id, title, format, preset, origin, status, step, progress, error, cost_usd, "
                   "output_json, created_at, updated_at FROM jobs ORDER BY id DESC LIMIT ?", limit)
    for r in rows:
        r["output"] = json.loads(r.pop("output_json")) if r.get("output_json") else None
    return rows


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int) -> dict:
    j = db.job(job_id)
    if not j:
        raise HTTPException(404)
    return _job_out(j)


def _check_writer(preset: str | None = None, overrides: dict | None = None, needs_writer: bool = True) -> None:
    if needs_writer and (problem := settings.resolve(preset, overrides).writer_problem()):
        raise HTTPException(400, problem)


@app.post("/api/jobs")
def create_job(data: dict[str, Any] = Body(...)) -> dict:
    _check_writer(data.get("preset"), data.get("overrides"))
    fmt = data.get("format") or None
    title = (data.get("title") or "").strip()
    topic_id = data.get("topic_id")
    if topic_id:
        t = db.row("SELECT * FROM topics WHERE id=?", topic_id)
        if not t:
            raise HTTPException(404, "topic not found")
        title, fmt = t["title"], t["format"]
        with db.tx() as c:
            c.execute("UPDATE topics SET status='used' WHERE id=?", (topic_id,))
    elif not title:
        topics.ensure_pool(settings.load())
        t = db.take_topic(fmt) or db.take_topic()
        if not t:
            raise HTTPException(400, "no topics available; refill the pool or type a topic")
        title, fmt, topic_id = t["title"], t["format"], t["id"]
    fmt = fmt or "timeline_story"
    if fmt not in formats.all_formats():
        raise HTTPException(400, f"unknown format {fmt}")
    job_id = worker.enqueue(title, fmt, data.get("preset") or "autopilot", data.get("overrides") or {},
                            topic_id=topic_id)
    return {"id": job_id}


@app.post("/api/jobs/upload")
async def create_upload_job(file: UploadFile = File(...), direction: str = Form(""),
                            preset: str = Form("autopilot"), target_seconds: int = Form(0)) -> dict:
    _check_writer(preset)
    up = settings.DATA / "uploads"
    up.mkdir(parents=True, exist_ok=True)
    dest = up / f"{int(time.time())}_{Path(file.filename or 'upload.mp4').name}"
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    if not media.has_video(dest):
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "that file has no video stream")
    overrides: dict[str, Any] = {"extra_script_notes": direction}
    if target_seconds:
        overrides["target_seconds"] = target_seconds
    else:
        overrides["target_seconds"] = int(min(180, max(20, media.duration(dest))))
    title = direction.strip()[:80] or Path(file.filename or "my footage").stem
    job_id = worker.enqueue(title, "footage_narration", preset, overrides, source_video=str(dest))
    return {"id": job_id}


@app.post("/api/jobs/{job_id}/approve")
def approve(job_id: int, data: dict[str, Any] = Body(default={})) -> dict:
    j = db.job(job_id)
    if not j:
        raise HTTPException(404)
    if data.get("script"):
        db.update_job(job_id, script_json=data["script"])
    db.update_job(job_id, status="queued", step="Approved")
    worker.wake()
    return {"ok": True}


@app.post("/api/jobs/{job_id}/cancel")
def cancel(job_id: int) -> dict:
    db.update_job(job_id, status="cancelled", step="Cancelled")
    return {"ok": True}


@app.post("/api/jobs/{job_id}/retry")
def retry(job_id: int) -> dict:
    j = db.job(job_id)
    if not j:
        raise HTTPException(404)
    # Re-resolve so fixes made in Settings since the first attempt (keys, provider) apply
    overrides = json.loads(j.get("overrides_json") or "{}")
    _check_writer(j["preset"], overrides, needs_writer=not j["script_json"])
    resolved = settings.resolve(j["preset"], overrides)
    db.update_job(job_id, status="queued", error=None, progress=0, step="Queued",
                  settings_json=json.dumps(resolved.model_dump()))
    worker.wake()
    return {"ok": True}


@app.post("/api/jobs/{job_id}/remake")
def remake(job_id: int, data: dict[str, Any] = Body(default={})) -> dict:
    """New job reusing (optionally edited) script with new settings."""
    j = db.job(job_id)
    if not j:
        raise HTTPException(404)
    script = data.get("script") or (json.loads(j["script_json"]) if j["script_json"] else None)
    if data.get("new_script"):
        script = None
    if data.get("overrides") is None:
        data["overrides"] = json.loads(j.get("overrides_json") or "{}")
    _check_writer(data.get("preset") or j["preset"], data["overrides"], needs_writer=script is None)
    new_id = worker.enqueue(j["title"], j["format"], data.get("preset") or j["preset"],
                            data.get("overrides") or {}, topic_id=j["topic_id"],
                            source_video=j["source_video"], script=script)
    return {"id": new_id}


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: int) -> dict:
    shutil.rmtree(settings.DATA / "jobs" / str(job_id), ignore_errors=True)
    with db.tx() as c:
        c.execute("DELETE FROM jobs WHERE id=?", (job_id,))
    return {"ok": True}


@app.get("/media/{job_id}/{kind}/{name}")
def job_media(job_id: int, kind: str, name: str) -> FileResponse:
    j = db.job(job_id)
    if not j or not j["output_json"]:
        raise HTTPException(404)
    out = json.loads(j["output_json"])
    path = (out.get(kind) or {}).get(name)
    if not path or not Path(path).exists():
        raise HTTPException(404)
    return FileResponse(path)


# Footage library -----------------------------------------------------------

@app.get("/api/library")
def library() -> list[dict]:
    return db.rows("SELECT * FROM library ORDER BY id DESC")


@app.post("/api/library")
async def add_library(file: UploadFile = File(...), tags: str = Form(""), kind: str = Form("")) -> dict:
    lib = settings.DATA / "library"
    lib.mkdir(parents=True, exist_ok=True)
    dest = lib / f"{int(time.time())}_{Path(file.filename or 'clip').name}"
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    ctype = file.content_type or ""
    kind = kind or ("music" if ctype.startswith("audio") else "image" if ctype.startswith("image") else "video")
    dur = None
    if kind in ("video", "music"):
        try:
            dur = media.duration(dest)
        except Exception:
            dest.unlink(missing_ok=True)
            raise HTTPException(400, "couldn't read that file")
    with db.tx() as c:
        c.execute("INSERT INTO library (path, kind, tags, duration, created_at) VALUES (?,?,?,?,?)",
                  (str(dest), kind, tags, dur, time.time()))
    return {"ok": True}


@app.delete("/api/library/{item_id}")
def del_library(item_id: int) -> dict:
    r = db.row("SELECT path FROM library WHERE id=?", item_id)
    if r:
        Path(r["path"]).unlink(missing_ok=True)
        with db.tx() as c:
            c.execute("DELETE FROM library WHERE id=?", (item_id,))
    return {"ok": True}
