"""Polite HTTP client: robots.txt, per-host rate limiting, retries with backoff.

Each fetch can be archived through ``btv.archive`` by the caller; this module
only deals with the network.
"""

from __future__ import annotations

import time
import urllib.robotparser
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit

import httpx

from btv.config import Config, get_config

RETRY_STATUSES = {429, 500, 502, 503, 504}


class RobotsDisallowed(Exception):
    pass


class FetchError(Exception):
    def __init__(self, url: str, message: str, status: int | None = None):
        super().__init__(f"{url}: {message}")
        self.url = url
        self.status = status


@dataclass
class FetchResult:
    url: str
    status: int
    body: bytes
    content_type: str | None

    def json(self):
        import json

        return json.loads(self.body)

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class PoliteClient:
    def __init__(
        self,
        cfg: Config | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        max_attempts: int = 4,
        backoff_base: float = 2.0,
        respect_robots: bool = True,
    ):
        self.cfg = cfg or get_config()
        self._client = httpx.Client(
            headers={"User-Agent": self.cfg.user_agent, "Accept-Language": "en-US,en;q=0.8"},
            timeout=httpx.Timeout(30.0, connect=15.0),
            follow_redirects=True,
            transport=transport,
        )
        self._sleep = sleep
        self._clock = clock
        self.max_attempts = max_attempts
        self.backoff_base = backoff_base
        self.respect_robots = respect_robots
        self._last_hit: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    # -- robots -----------------------------------------------------------
    def _robots_for(self, url: str) -> urllib.robotparser.RobotFileParser | None:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            rp: urllib.robotparser.RobotFileParser | None = urllib.robotparser.RobotFileParser()
            try:
                self._throttle(parts.netloc)
                r = self._client.get(origin + "/robots.txt")
                if r.status_code >= 400:
                    rp = None  # no robots.txt => everything allowed
                else:
                    rp.parse(r.text.splitlines())
            except httpx.HTTPError:
                rp = None
            self._robots[origin] = rp
        return self._robots[origin]

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        rp = self._robots_for(url)
        return True if rp is None else rp.can_fetch(self.cfg.user_agent, url)

    # -- rate limiting ------------------------------------------------------
    def _throttle(self, host: str) -> None:
        last = self._last_hit.get(host)
        if last is not None:
            wait = self.cfg.per_host_interval - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        self._last_hit[host] = self._clock()

    # -- fetch ----------------------------------------------------------------
    def request(self, method: str, url: str, **kwargs) -> FetchResult:
        if not self.allowed(url):
            raise RobotsDisallowed(url)
        host = urlsplit(url).netloc
        last_exc: str = ""
        status: int | None = None
        for attempt in range(1, self.max_attempts + 1):
            self._throttle(host)
            try:
                r = self._client.request(method, url, **kwargs)
            except httpx.HTTPError as exc:
                last_exc, status = f"{type(exc).__name__}: {exc}", None
            else:
                status = r.status_code
                if status not in RETRY_STATUSES:
                    if status >= 400:
                        raise FetchError(url, f"HTTP {status}", status)
                    return FetchResult(str(r.url), status, r.content, r.headers.get("content-type"))
                last_exc = f"HTTP {status}"
                retry_after = r.headers.get("retry-after")
                if retry_after and retry_after.isdigit() and attempt < self.max_attempts:
                    self._sleep(min(float(retry_after), 600.0))
                    continue
            if attempt < self.max_attempts:
                self._sleep(self.backoff_base ** attempt)
        raise FetchError(url, f"gave up after {self.max_attempts} attempts ({last_exc})", status)

    def get(self, url: str, **kwargs) -> FetchResult:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> FetchResult:
        return self.request("POST", url, **kwargs)

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
