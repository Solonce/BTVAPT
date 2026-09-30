"""Command-line interface: ``btv --help``."""

from __future__ import annotations

import json
import time

import typer
from rich.console import Console
from rich.live import Live
from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from btv import settings as user_settings
from btv.config import get_config
from btv.db import init_db, session_scope
from btv.health import source_health
from btv.jobs import runner
from btv.models import Job, Source
from btv.sources.registry import set_enabled, sync_sources

app = typer.Typer(no_args_is_help=True, help="Burlington VT apartment tracker")
sources_app = typer.Typer(no_args_is_help=True, help="Manage sources and kill switches")
settings_app = typer.Typer(no_args_is_help=True, help="View/change runtime settings")
app.add_typer(sources_app, name="sources")
app.add_typer(settings_app, name="settings")
console = Console()
# Progress bars go to stderr so `--json` output on stdout stays parseable.
progress_console = Console(stderr=True)


def _dump(obj) -> None:
    typer.echo(json.dumps(obj, indent=2, default=str))


def _progress() -> Progress:
    return Progress(
        TextColumn("[bold]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TextColumn("{task.completed:.0f}/{task.total}"),
        TimeElapsedColumn(),
        TextColumn("[dim]{task.fields[step]}"),
        console=progress_console,
    )


def _run_with_progress(kind: str, source_id: str | None = None, params: dict | None = None) -> Job:
    cfg = get_config()
    with _progress() as prog:
        task = prog.add_task(f"{kind} {source_id or ''}".strip(), total=None, step="")

        def update(ctx: runner.JobContext) -> None:
            prog.update(task, total=ctx.total, completed=ctx.done, step=ctx.step_text or "")

        j = runner.run_inline(kind, source_id=source_id, params=params, cfg=cfg, on_update=update)
        if j.status == "succeeded" and j.total:
            prog.update(task, completed=j.total)
    return j


def _report(j: Job, as_json: bool) -> None:
    d = runner.job_to_dict(j)
    if as_json:
        _dump(d)
    else:
        colour = "green" if j.status == "succeeded" else "red"
        console.print(f"job #{j.id} [{colour}]{j.status}[/]" + (f": {j.error}" if j.error else ""))
        if j.result:
            console.print_json(data=j.result)
    if j.status != "succeeded":
        raise typer.Exit(1)


# ------------------------------------------------------------------- setup


@app.command()
def init() -> None:
    """Create/migrate the database and load config/sources.toml."""
    cfg = get_config()
    init_db(cfg)
    with session_scope(cfg) as s:
        report = sync_sources(s, cfg.sources_file)
    console.print(f"database ready at {cfg.db_path}")
    _dump(report)


@app.command()
def serve(worker: bool = typer.Option(True, help="Run the background scheduler/worker in-process")) -> None:
    """Run the JSON API + status page (and the worker)."""
    import uvicorn

    from btv.api.app import create_app

    cfg = get_config()
    uvicorn.run(create_app(cfg, start_worker=worker), host=cfg.host, port=cfg.port, log_level="info")


@app.command("worker")
def worker_cmd() -> None:
    """Run only the scheduler/worker loop (no HTTP)."""
    import logging

    from btv.jobs.worker import Worker

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = get_config()
    init_db(cfg)
    Worker(cfg).run_forever()


# ------------------------------------------------------------------ jobs


@app.command()
def scrape(
    source_id: str,
    queue: bool = typer.Option(False, "--queue", help="Enqueue for the worker instead of running here"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Scrape one source now, with a live progress bar."""
    if queue:
        _dump({"id": runner.enqueue("scrape", source_id=source_id)})
        return
    _report(_run_with_progress("scrape", source_id), as_json)


@app.command()
def backup(as_json: bool = typer.Option(False, "--json")) -> None:
    """Back up the database now (with retention pruning)."""
    _report(_run_with_progress("backup"), as_json)


@app.command("run")
def run_cmd(
    kind: str,
    source_id: str = typer.Option(None, "--source"),
    params: str = typer.Option("{}", help="JSON params"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Run any registered job kind inline."""
    if kind not in runner.registered_kinds():
        raise typer.BadParameter(f"known kinds: {runner.registered_kinds()}")
    _report(_run_with_progress(kind, source_id, json.loads(params)), as_json)


@app.command()
def jobs(
    watch: bool = typer.Option(False, "--watch", "-w", help="Live progress of active jobs"),
    job_id: int = typer.Option(None, "--id"),
    limit: int = 20,
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List jobs, show one job, or watch active jobs update live."""
    cfg = get_config()
    if job_id is not None:
        with session_scope(cfg) as s:
            j = s.get(Job, job_id)
            if j is None:
                raise typer.BadParameter("no such job")
            _dump(runner.job_to_dict(j, events=50))
        return
    if watch:
        _watch(cfg)
        return
    with session_scope(cfg) as s:
        rows = [runner.job_to_dict(j) for j in s.query(Job).order_by(Job.id.desc()).limit(limit)]
    if as_json:
        _dump(rows)
        return
    t = Table("id", "kind", "source", "status", "progress", "step", "finished", "error")
    for d in rows:
        pct = f"{d['percent']}%" if d["percent"] is not None else ""
        t.add_row(str(d["id"]), d["kind"], d["source_id"] or "", d["status"], pct,
                  (d["step"] or "")[:40], d["finished_at"] or "", (d["error"] or "")[:60])
    console.print(t)


@app.command()
def cancel(job_id: int) -> None:
    """Cancel a queued job or ask a running one to stop."""
    _dump({"id": job_id, "status": runner.request_cancel(job_id)})


def _watch(cfg) -> None:
    prog = _progress()
    tasks: dict[int, int] = {}
    with Live(prog, console=console, refresh_per_second=4):
        while True:
            with session_scope(cfg) as s:
                active = s.query(Job).filter(Job.status.in_(runner.ACTIVE)).order_by(Job.id).all()
                seen = set()
                for j in active:
                    seen.add(j.id)
                    desc = f"#{j.id} {j.kind} {j.source_id or ''}".strip()
                    if j.id not in tasks:
                        tasks[j.id] = prog.add_task(desc, total=j.total, step="")
                    prog.update(tasks[j.id], total=j.total, completed=j.done,
                                step=f"{j.status}: {j.step or ''}")
                for jid in list(tasks):
                    if jid not in seen:
                        j = s.get(Job, jid)
                        prog.update(tasks[jid], completed=j.total or j.done, step=j.status)
                        prog.stop_task(tasks.pop(jid))
            time.sleep(1)


# ---------------------------------------------------------------- health


@app.command()
def health(as_json: bool = typer.Option(False, "--json")) -> None:
    """Per-source health; exits 1 if any source is failing/degraded/stale."""
    cfg = get_config()
    with session_scope(cfg) as s:
        rows = [source_health(src, cfg) for src in s.query(Source).order_by(Source.id)]
    bad = [r for r in rows if r["state"] in ("failing", "degraded", "stale")]
    if as_json:
        _dump({"ok": not bad, "sources": rows})
    else:
        colours = {"ok": "green", "disabled": "dim", "never_run": "yellow", "stale": "yellow",
                   "degraded": "yellow", "failing": "red"}
        t = Table("source", "state", "last run", "last success", "count", "reasons")
        for r in rows:
            c = colours.get(r["state"], "white")
            t.add_row(r["source_id"], f"[{c}]{r['state']}[/]", r["last_run_at"] or "never",
                      r["last_success_at"] or "never", str(r["last_count"] or ""), "; ".join(r["reasons"]))
        console.print(t)
    if bad:
        raise typer.Exit(1)


# --------------------------------------------------------------- sources


@sources_app.command("sync")
def sources_sync() -> None:
    """Reload config/sources.toml into the DB."""
    cfg = get_config()
    with session_scope(cfg) as s:
        _dump(sync_sources(s, cfg.sources_file))


@sources_app.command("list")
def sources_list(as_json: bool = typer.Option(False, "--json")) -> None:
    cfg = get_config()
    with session_scope(cfg) as s:
        rows = [{**source_health(src, cfg), "config": src.config} for src in s.query(Source).order_by(Source.id)]
    if as_json:
        _dump(rows)
        return
    t = Table("id", "name", "platform", "enabled", "every", "state")
    for r in rows:
        t.add_row(r["source_id"], r["name"], r["platform"], "yes" if r["enabled"] else "[red]no[/]",
                  f"{r['interval_minutes']}m", r["state"])
    console.print(t)


@sources_app.command("disable")
def sources_disable(source_id: str, reason: str = typer.Option("disabled manually", "--reason")) -> None:
    """Kill switch: stop scheduling a source."""
    with session_scope() as s:
        set_enabled(s, source_id, False, reason)
    console.print(f"{source_id} disabled")


@sources_app.command("enable")
def sources_enable(source_id: str) -> None:
    with session_scope() as s:
        set_enabled(s, source_id, True)
    console.print(f"{source_id} enabled")


# -------------------------------------------------------------- settings


@settings_app.command("get")
def settings_get() -> None:
    with session_scope() as s:
        _dump(user_settings.get_all(s))


@settings_app.command("set")
def settings_set(key: str, value: str) -> None:
    """e.g. ``btv settings set target_move_in 2027-06-01``"""
    with session_scope() as s:
        try:
            user_settings.set_value(s, key, value)
        except (KeyError, ValueError) as exc:
            raise typer.BadParameter(str(exc)) from exc
        _dump(user_settings.get_all(s))


if __name__ == "__main__":
    app()
