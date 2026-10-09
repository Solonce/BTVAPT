"""Shared HTTP helpers (honour SSL_CERT_FILE / HTTPS_PROXY for boxes behind a proxy)."""
from __future__ import annotations

import os
import ssl
from pathlib import Path

import httpx

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def client(timeout: float = 60) -> httpx.Client:
    return httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": UA})


def ssl_context() -> ssl.SSLContext:
    ca = os.environ.get("SSL_CERT_FILE")
    return ssl.create_default_context(cafile=ca) if ca else ssl.create_default_context()


def download(url: str, dest: Path, timeout: float = 300, headers: dict | None = None) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with client(timeout) as c, c.stream("GET", url, headers=headers or {}) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_bytes(1 << 16):
                f.write(chunk)
    if dest.stat().st_size == 0:
        raise RuntimeError(f"empty download from {url}")
    return dest
