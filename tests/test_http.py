from dataclasses import replace

import httpx
import pytest

from btv.http import FetchError, PoliteClient, RobotsDisallowed


def client(cfg, handler, **kw):
    sleeps = []
    c = PoliteClient(cfg, transport=httpx.MockTransport(handler), sleep=sleeps.append, **kw)
    return c, sleeps


def no_robots(inner):
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return inner(req)
    return handler


def test_retries_then_succeeds(cfg):
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] < 3 else httpx.Response(200, json={"ok": True})

    c, sleeps = client(cfg, no_robots(handler))
    assert c.get("https://x.test/api").json() == {"ok": True}
    assert calls["n"] == 3
    assert [s for s in sleeps if s >= 2] == [2.0, 4.0]


def test_gives_up_and_does_not_retry_404(cfg):
    c, _ = client(cfg, no_robots(lambda req: httpx.Response(500 if req.url.path == "/err" else 404)), max_attempts=2)
    with pytest.raises(FetchError, match="gave up after 2"):
        c.get("https://x.test/err")
    with pytest.raises(FetchError, match="HTTP 404"):
        c.get("https://x.test/missing")


def test_robots_disallow(cfg):
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
        return httpx.Response(200, text="hi")

    c, _ = client(cfg, handler)
    assert c.get("https://x.test/public").text == "hi"
    with pytest.raises(RobotsDisallowed):
        c.get("https://x.test/private/page")


def test_per_host_throttle(cfg):
    sleeps = []
    c = PoliteClient(replace(cfg, per_host_interval=5.0),
                     transport=httpx.MockTransport(no_robots(lambda req: httpx.Response(200))),
                     sleep=sleeps.append, clock=lambda: 0.0)
    c.get("https://x.test/a")
    c.get("https://x.test/b")
    assert sleeps and all(s == 5.0 for s in sleeps)
