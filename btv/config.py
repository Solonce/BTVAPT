"""Process configuration: filesystem paths and service knobs.

Loaded from ``btv.toml`` (path overridable with ``BTV_CONFIG``) with
``BTV_*`` environment variables taking precedence. User-facing settings that
change at runtime (target move-in date, near-miss window) live in the DB
``settings`` table instead; see ``btv.settings``.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class Config:
    data_dir: Path = Path("data")
    sources_file: Path = Path("config/sources.toml")
    host: str = "127.0.0.1"
    port: int = 8321
    # Browser UA: Buildium redirects non-browser agents to a login page.
    user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/129.0.0.0 Safari/537.36"
    )
    # Minimum seconds between requests to the same host.
    per_host_interval: float = 5.0
    # Backup retention.
    keep_daily: int = 14
    keep_weekly: int = 8
    # A source whose last success is older than this many schedule intervals is "stale".
    stale_after_intervals: float = 3.0
    extra: dict = field(default_factory=dict)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "btv.sqlite"

    @property
    def archive_dir(self) -> Path:
        return self.data_dir / "archive"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def lock_path(self) -> Path:
        return self.data_dir / "heavy.lock"

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"


_FIELDS = {
    "data_dir": Path,
    "sources_file": Path,
    "host": str,
    "port": int,
    "user_agent": str,
    "per_host_interval": float,
    "keep_daily": int,
    "keep_weekly": int,
    "stale_after_intervals": float,
}


def load_config(path: str | os.PathLike | None = None) -> Config:
    path = Path(path or os.environ.get("BTV_CONFIG", "btv.toml"))
    raw: dict = {}
    if path.exists():
        raw = tomllib.loads(path.read_text())
    kwargs = {}
    for name, typ in _FIELDS.items():
        env = os.environ.get(f"BTV_{name.upper()}")
        if env is not None:
            kwargs[name] = typ(env)
        elif name in raw:
            kwargs[name] = typ(raw[name])
    extra = {k: v for k, v in raw.items() if k not in _FIELDS}
    return Config(**kwargs, extra=extra)


@lru_cache(maxsize=1)
def get_config() -> Config:
    return load_config()
