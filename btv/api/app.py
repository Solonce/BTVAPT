"""JSON API plus the static status/map page.

Served only on the Tailscale interface; no auth by design.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from btv import settings as user_settings
from btv.config import Config, get_config
from btv.db import init_db, session_scope
from btv.health import source_health
from btv.jobs import runner
from btv.models import Job, ScrapeRun, Source
from btv.sources.registry import set_enabled

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def create_app(cfg: Config | None = None, start_worker: bool = True) -> FastAPI:
    cfg = cfg or get_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(cfg)
        worker = None
        if start_worker:
            from btv.jobs.worker import Worker

            worker = Worker(cfg)
            worker.start_thread()
        yield
        if worker:
            worker.stop()

    app = FastAPI(title="BTVAPT", lifespan=lifespan)

    # ------------------------------------------------------------- jobs
    @app.get("/api/jobs")
    def list_jobs(status: str | None = None, kind: str | None = None, limit: int = Query(50, le=500)):
        with session_scope(cfg) as s:
            q = s.query(Job)
            if status:
                q = q.filter(Job.status.in_(status.split(",")))
            if kind:
                q = q.filter(Job.kind == kind)
            jobs = q.order_by(Job.id.desc()).limit(limit).all()
            active = [runner.job_to_dict(j) for j in jobs if j.status in runner.ACTIVE]
            return {"active": active, "jobs": [runner.job_to_dict(j) for j in jobs]}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: int, events: int = 50):
        with session_scope(cfg) as s:
            j = s.get(Job, job_id)
            if j is None:
                raise HTTPException(404, "no such job")
            return runner.job_to_dict(j, events=events)

    @app.post("/api/jobs")
    def create_job(kind: str = Body(...), source_id: str | None = Body(None), params: dict = Body(default_factory=dict)):
        try:
            job_id = runner.enqueue(kind, source_id=source_id, params=params, cfg=cfg)
        except KeyError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"id": job_id}

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: int):
        try:
            return {"id": job_id, "status": runner.request_cancel(job_id, cfg)}
        except KeyError as exc:
            raise HTTPException(404, "no such job") from exc

    # ----------------------------------------------------------- health
    @app.get("/api/health")
    def health():
        with session_scope(cfg) as s:
            sources = [source_health(src, cfg) for src in s.query(Source).order_by(Source.id).all()]
            last_backup = (
                s.query(Job).filter(Job.kind == "backup").order_by(Job.id.desc()).first()
            )
            active = s.query(Job).filter(Job.status.in_(runner.ACTIVE)).count()
            bad = [x for x in sources if x["state"] in ("failing", "degraded", "stale")]
            return {
                "ok": not bad and (last_backup is None or last_backup.status != "failed"),
                "sources": sources,
                "problem_sources": [x["source_id"] for x in bad],
                "last_backup": runner.job_to_dict(last_backup) if last_backup else None,
                "active_jobs": active,
            }

    # ---------------------------------------------------------- sources
    @app.get("/api/sources")
    def list_sources():
        with session_scope(cfg) as s:
            return [
                {**source_health(src, cfg), "config": src.config}
                for src in s.query(Source).order_by(Source.id).all()
            ]

    @app.get("/api/sources/{source_id}/runs")
    def source_runs(source_id: str, limit: int = Query(20, le=200)):
        with session_scope(cfg) as s:
            runs = (
                s.query(ScrapeRun).filter(ScrapeRun.source_id == source_id)
                .order_by(ScrapeRun.id.desc()).limit(limit).all()
            )
            return [
                {
                    "id": r.id, "job_id": r.job_id, "status": r.status, "healthy": r.healthy,
                    "health_notes": r.health_notes, "listings_found": r.listings_found,
                    "error": r.error, "started_at": runner._iso(r.started_at),
                    "finished_at": runner._iso(r.finished_at),
                }
                for r in runs
            ]

    @app.post("/api/sources/{source_id}/enabled")
    def toggle_source(source_id: str, enabled: bool = Body(..., embed=True), reason: str | None = Body(None, embed=True)):
        with session_scope(cfg) as s:
            try:
                src = set_enabled(s, source_id, enabled, reason)
            except KeyError as exc:
                raise HTTPException(404, str(exc)) from exc
            return source_health(src, cfg)

    # --------------------------------------------------------- settings
    @app.get("/api/settings")
    def get_settings():
        with session_scope(cfg) as s:
            return user_settings.get_all(s)

    @app.put("/api/settings")
    def put_settings(values: dict = Body(...)):
        with session_scope(cfg) as s:
            try:
                for k, v in values.items():
                    user_settings.set_value(s, k, v)
            except (KeyError, ValueError) as exc:
                raise HTTPException(400, str(exc)) from exc
            return user_settings.get_all(s)

    # ----------------------------------------------------------- static
    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
