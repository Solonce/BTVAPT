"""JSON API plus the static status/map page.

Served only on the Tailscale interface; no auth by design.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from btv import actions, views
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

    # ------------------------------------------------------------ units
    def _act(fn):
        try:
            return fn()
        except actions.ActionError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/units")
    def units(include_gone: bool = False, match: str | None = None, min_rent: int | None = None,
              max_rent: int | None = None, min_beds: float | None = None, tag: str | None = None,
              city: str | None = None, q: str | None = None):
        """All units with effective availability; filters are optional conveniences."""
        with session_scope(cfg) as s:
            data = views.list_units(s, include_gone=include_gone)
        us = data["units"]
        if match:
            wanted = set(match.split(","))
            us = [u for u in us if u["availability"]["match"] in wanted]
        if min_rent is not None:
            us = [u for u in us if u["rent"] is None or u["rent"] >= min_rent]
        if max_rent is not None:
            us = [u for u in us if u["rent"] is None or u["rent"] <= max_rent]
        if min_beds is not None:
            us = [u for u in us if u["beds"] is not None and u["beds"] >= min_beds]
        if tag:
            us = [u for u in us if tag.lower() in u["tags"]]
        if city:
            us = [u for u in us if (u["city"] or "").lower() == city.lower()]
        if q:
            ql = q.lower()
            us = [u for u in us if ql in " ".join(str(x) for x in (u["address"], u["title"], u["unit"],
                                                                   " ".join(u["amenities"]))).lower()]
        data["units"], data["count"] = us, len(us)
        return data

    @app.get("/api/units/{unit_id}")
    def unit(unit_id: int):
        with session_scope(cfg) as s:
            d = views.unit_detail(s, unit_id)
            if d is None:
                raise HTTPException(404, "no such unit")
            return d

    @app.put("/api/units/{unit_id}/prefs")
    def unit_prefs(unit_id: int, values: dict = Body(...)):
        allowed = {k: v for k, v in values.items() if k in ("rating", "status", "notes")}
        with session_scope(cfg) as s:
            p = _act(lambda: actions.set_prefs(s, unit_id, **allowed))
            return {"rating": p.rating, "status": p.status, "notes": p.notes}

    @app.post("/api/units/{unit_id}/tags")
    def unit_add_tag(unit_id: int, tag: str = Body(..., embed=True)):
        with session_scope(cfg) as s:
            return {"tags": _act(lambda: actions.add_tag(s, unit_id, tag))}

    @app.delete("/api/units/{unit_id}/tags/{tag}")
    def unit_remove_tag(unit_id: int, tag: str):
        with session_scope(cfg) as s:
            return {"tags": _act(lambda: actions.remove_tag(s, unit_id, tag))}

    @app.get("/api/tags")
    def tags():
        with session_scope(cfg) as s:
            return actions.all_tags(s)

    @app.post("/api/units/merge")
    def units_merge(into_id: int = Body(...), merged_id: int = Body(...), reason: str | None = Body(None)):
        with session_scope(cfg) as s:
            log = _act(lambda: actions.merge_units(s, into_id, merged_id, reason))
            return {"merge_id": log.id, "unit_id": into_id}

    @app.post("/api/listings/{source_listing_id}/unlink")
    def listing_unlink(source_listing_id: int, reason: str | None = Body(None, embed=True)):
        with session_scope(cfg) as s:
            log = _act(lambda: actions.unlink_listing(s, source_listing_id, reason))
            return {"merge_id": log.id, "unit_id": log.subject["to_unit_id"]}

    @app.post("/api/merges/{merge_id}/undo")
    def merge_undo(merge_id: int):
        with session_scope(cfg) as s:
            log = _act(lambda: actions.undo(s, merge_id))
            return {"merge_id": log.id, "undone_at": runner._iso(log.undone_at)}

    # --------------------------------------------------------- contacts
    @app.get("/api/contacts")
    def contacts(multi_only: bool = False):
        with session_scope(cfg) as s:
            rows = views.list_contacts(s)
        return [r for r in rows if r["multi_property"]] if multi_only else rows

    @app.get("/api/contacts/{contact_id}")
    def contact(contact_id: int):
        with session_scope(cfg) as s:
            d = views.contact_detail(s, contact_id)
            if d is None:
                raise HTTPException(404, "no such contact")
            return d

    @app.put("/api/contacts/{contact_id}")
    def contact_update(contact_id: int, notes: str | None = Body(None), name: str | None = Body(None)):
        with session_scope(cfg) as s:
            c = _act(lambda: actions.set_contact_notes(s, contact_id, notes, name))
            return {"id": c.id, "name": c.display_name, "notes": c.notes}

    @app.post("/api/contacts/{contact_id}/outreach")
    def contact_outreach(contact_id: int, values: dict = Body(...)):
        with session_scope(cfg) as s:
            o = _act(lambda: actions.add_outreach(s, contact_id, **{k: v for k, v in values.items() if k in (
                "channel", "direction", "summary", "outcome", "follow_up_on", "unit_id")}))
            return {"id": o.id}

    @app.post("/api/contacts/merge")
    def contacts_merge(into_id: int = Body(...), merged_id: int = Body(...), reason: str | None = Body(None)):
        with session_scope(cfg) as s:
            log = _act(lambda: actions.merge_contacts(s, into_id, merged_id, reason))
            return {"merge_id": log.id}

    @app.post("/api/mentions/{mention_id}/review")
    def mention_review(mention_id: int, decision: str = Body(..., embed=True)):
        with session_scope(cfg) as s:
            m = _act(lambda: actions.review_mention(s, mention_id, decision))
            return {"id": m.id, "review_state": m.review_state}

    @app.get("/api/owners")
    def owners():
        with session_scope(cfg) as s:
            return views.list_owners(s)

    # ------------------------------------------------------------ leads
    @app.get("/api/leads")
    def leads(triage: str | None = None):
        with session_scope(cfg) as s:
            rows = views.list_leads(s)
        return [r for r in rows if r["triage"] in triage.split(",")] if triage else rows

    @app.post("/api/leads")
    def add_lead(text: str = Body(""), url: str | None = Body(None), title: str | None = Body(None),
                 author: str | None = Body(None)):
        """Save a post from anywhere (bookmarklet, paste). Runs the full pipeline."""
        from btv.inbox import ingest_text

        with session_scope(cfg) as s:
            try:
                sl = ingest_text(s, text, url=url, title=title, author=author)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            return {"source_listing_id": sl.id, "unit_id": sl.unit_id}

    @app.put("/api/leads/{source_listing_id}")
    def update_lead(source_listing_id: int, triage: str | None = Body(None), address: str | None = Body(None)):
        from btv.models import ListingSnapshot, SourceListing
        from btv.pipeline import link_listing

        needs_geocode = False
        with session_scope(cfg) as s:
            sl = s.get(SourceListing, source_listing_id)
            if sl is None:
                raise HTTPException(404, "no such lead")
            if triage is not None:
                if triage not in ("new", "saved", "contacted", "dismissed"):
                    raise HTTPException(400, "triage must be new/saved/contacted/dismissed")
                sl.triage = triage
            if address is not None:
                sl.address_override, sl.link_locked = address.strip() or None, False
                snap = s.get(ListingSnapshot, sl.latest_snapshot_id)
                unit = link_listing(s, sl, snap)
                s.flush()
                if address.strip() and unit is None:
                    raise HTTPException(400, "couldn't understand that address")
                needs_geocode = unit is not None and unit.building.lat is None
            result = {"source_listing_id": sl.id, "triage": sl.triage, "unit_id": sl.unit_id}
        if needs_geocode:  # after commit: enqueue opens its own write transaction
            runner.enqueue("geocode", cfg=cfg)
        return result

    @app.get("/api/companies")
    def companies():
        with session_scope(cfg) as s:
            return views.list_companies(s)

    # ----------------------------------------------------------- static
    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
