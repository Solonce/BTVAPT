"""Video formats are data: one TOML file per format in formats/.

Drop a new file in that folder (or create one in the UI) and the topic
generator, script writer and autopilot pick it up with no code changes.
"""
from __future__ import annotations

import re
import tomllib
from typing import Any

from .settings import FORMATS_DIR

FIELDS = ("label", "description", "examples", "structure", "tone", "visual_style",
          "recurring_character", "uses_movies", "uses_source_video")


def all_formats() -> dict[str, dict[str, Any]]:
    out = {}
    for p in sorted(FORMATS_DIR.glob("*.toml")):
        try:
            data = tomllib.loads(p.read_text())
        except tomllib.TOMLDecodeError:
            continue
        data.setdefault("label", p.stem)
        data.setdefault("examples", [])
        data.setdefault("structure", "")
        data.setdefault("tone", "")
        data.setdefault("visual_style", "cinematic")
        out[p.stem] = data
    return out


def get(name: str) -> dict[str, Any]:
    f = all_formats().get(name)
    if not f:
        raise KeyError(f"unknown format {name!r}")
    return f


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    s = str(v)
    if "\n" in s:
        return '"""\n' + s.replace('"""', "'''") + '\n"""'
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def save(name: str, data: dict[str, Any]) -> str:
    slug = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_") or "custom"
    lines = [f"{k} = {_toml_value(data[k])}" for k in FIELDS if k in data and data[k] not in (None, "")]
    FORMATS_DIR.mkdir(parents=True, exist_ok=True)
    (FORMATS_DIR / f"{slug}.toml").write_text("\n".join(lines) + "\n")
    return slug
