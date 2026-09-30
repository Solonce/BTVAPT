"""SQLite engine/session setup."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from btv.config import Config, get_config


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    """Naive UTC timestamp; every datetime column in the DB is UTC."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


_engines: dict[str, Engine] = {}


def get_engine(cfg: Config | None = None) -> Engine:
    cfg = cfg or get_config()
    url = cfg.db_url
    if url not in _engines:
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, connect_args={"timeout": 30, "check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()

        _engines[url] = engine
    return _engines[url]


def session_factory(cfg: Config | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(cfg), expire_on_commit=False)


@contextmanager
def session_scope(cfg: Config | None = None) -> Iterator[Session]:
    session = session_factory(cfg)()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def init_db(cfg: Config | None = None) -> None:
    """Bring the schema to the latest Alembic revision."""
    from alembic import command
    from alembic.config import Config as AlembicConfig
    from pathlib import Path

    cfg = cfg or get_config()
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parent.parent
    acfg = AlembicConfig(str(root / "alembic.ini"))
    acfg.set_main_option("script_location", str(root / "alembic"))
    acfg.set_main_option("sqlalchemy.url", cfg.db_url)
    command.upgrade(acfg, "head")
