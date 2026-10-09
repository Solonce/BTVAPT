"""ffmpeg / ffprobe helpers."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


class FFmpegError(RuntimeError):
    pass


def run(args: list[str], cwd: Path | None = None, timeout: float = 1800) -> None:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args]
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise FFmpegError(f"ffmpeg failed: {p.stderr.strip()[-1500:]}")


def probe(path: str | Path, timeout: float = 60) -> dict:
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, timeout=timeout,
    )
    if p.returncode != 0:
        raise FFmpegError(f"ffprobe failed on {path}: {p.stderr.strip()[-500:]}")
    return json.loads(p.stdout)


def duration(path: str | Path) -> float:
    return float(probe(path)["format"].get("duration") or 0)


def has_video(path: str | Path) -> bool:
    return any(s.get("codec_type") == "video" for s in probe(path)["streams"])


def frame(src: str | Path, t: float, dest: Path, width: int = 512) -> Path:
    run(["-ss", f"{t:.2f}", "-i", str(src), "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", str(dest)],
        timeout=120)
    return dest


def check() -> list[str]:
    return [b for b in ("ffmpeg", "ffprobe") if not shutil.which(b)]
