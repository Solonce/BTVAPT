"""Settings, presets and paths.

Everything has an autopilot-friendly default. Presets are partial overrides
layered on top of the base settings; a job snapshots the fully resolved
settings when it is created, so editing settings never changes a job in flight.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

ROOT = Path(os.environ.get("STUDIO_HOME", Path(__file__).resolve().parent.parent))
DATA = ROOT / "data"
FORMATS_DIR = ROOT / "formats"
SETTINGS_FILE = DATA / "settings.json"
PRESETS_FILE = DATA / "presets.json"

SECRET_FIELDS = {
    "anthropic_api_key", "featherless_api_key", "elevenlabs_api_key", "fal_key",
    "pexels_api_key", "youtube_api_key", "tmdb_api_key",
}


class Settings(BaseModel):
    # Output
    output_dir: str = "output"
    vertical: bool = True              # 9:16 render
    horizontal: bool = True            # 16:9 render
    resolution: int = 1080             # short side in px
    fps: int = 30
    target_seconds: int = 120
    format_weights: dict[str, float] = Field(default_factory=lambda: {"timeline_story": 1.0})

    # Writing
    llm_provider: str = "claude"       # claude | featherless (any OpenAI-compatible API)
    featherless_model: str = "deepseek-ai/DeepSeek-V3-0324"
    featherless_vision_model: str = "Qwen/Qwen3-VL-30B-A3B-Instruct"   # used when frames are attached
    featherless_base_url: str = "https://api.featherless.ai/v1"
    llm_model: str = "claude-opus-5-5"
    llm_effort: str = "medium"
    trend_research: bool = True        # let Claude web-search for trending faceless videos when refilling topics
    extra_script_notes: str = ""       # free text appended to every script prompt

    # Voice
    tts_provider: str = "edge"         # edge (free) | elevenlabs
    edge_voice: str = "en-US-ChristopherNeural"
    edge_rate: str = "+8%"
    elevenlabs_voice_id: str = "pNInz6obpgDQGcFmaJgB"
    elevenlabs_model: str = "eleven_multilingual_v2"

    # Visuals
    image_provider: str = "pollinations"   # pollinations (free) | fal
    fal_image_model: str = "fal-ai/flux/schnell"
    video_provider: str = "none"           # none | fal
    fal_video_model: str = "fal-ai/kling-video/v2.1/standard/image-to-video"
    max_ai_video_clips: int = 2
    # Relative weights for where a beat's footage comes from when the script
    # doesn't force one. Sources without credentials/content are skipped.
    visual_mix: dict[str, float] = Field(default_factory=lambda: {
        "ai_image": 1.0, "stock": 0.0, "library": 0.0,
    })
    visual_style: str = ""             # overrides the format's style when set
    motion_intensity: float = 1.0      # 0 = static, 1 = normal, 2 = aggressive
    max_shot_seconds: float = 4.0      # longer beats get split into several shots

    # Local media server (vidsrc-style embed API on your network)
    mediaserver_enabled: bool = False
    mediaserver_base: str = "http://vidsrc.xyz"
    mediaserver_movie_path: str = "/embed/movie/{tmdb}"
    clip_seconds_min: float = 2.5

    # Captions / music
    captions: bool = True
    caption_style: str = "pop"         # pop (word-by-word highlight) | subtle
    caption_font: str = "DejaVu Sans"
    caption_words: int = 3
    caption_color: str = "#FFFFFF"
    caption_highlight: str = "#FFE14D"
    music: bool = False
    music_volume: float = 0.14

    # Autopilot
    autopilot: bool = False
    videos_per_day: int = 2
    topic_pool_min: int = 8
    require_approval: bool = False     # pause after the script for review

    # Budget
    monthly_budget_usd: float = 100.0
    per_video_max_usd: float = 4.0

    # Credentials (env vars with the upper-case name win)
    anthropic_api_key: str = ""
    featherless_api_key: str = ""
    elevenlabs_api_key: str = ""
    fal_key: str = ""
    pexels_api_key: str = ""
    youtube_api_key: str = ""
    tmdb_api_key: str = ""

    def secret(self, name: str) -> str:
        return os.environ.get(name.upper(), "") or getattr(self, name)

    def output_path(self) -> Path:
        p = Path(self.output_dir)
        return p if p.is_absolute() else ROOT / p


BUILTIN_PRESETS: dict[str, dict[str, Any]] = {
    "autopilot": {},
    "free": {
        "tts_provider": "edge", "image_provider": "pollinations",
        "video_provider": "none", "llm_model": "claude-sonnet-5-5",
    },
    "premium": {
        "tts_provider": "elevenlabs", "image_provider": "fal",
        "video_provider": "fal", "max_ai_video_clips": 4,
        "visual_mix": {"ai_image": 0.7, "stock": 0.3, "library": 0.0},
        "music": True,
    },
    "quick_short": {"target_seconds": 45, "horizontal": False, "max_shot_seconds": 2.5},
}

_lock = threading.Lock()


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def load() -> Settings:
    return Settings(**_read(SETTINGS_FILE))


def save(values: dict[str, Any]) -> Settings:
    with _lock:
        current = load().model_dump()
        for k, v in values.items():
            # A blank secret from the UI means "keep the stored one"
            if k in SECRET_FIELDS and v in ("", None, "••••"):
                continue
            if k in current:
                current[k] = v
        s = Settings(**current)
        DATA.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(s.model_dump(), indent=2))
        return s


def presets() -> dict[str, dict[str, Any]]:
    return {**BUILTIN_PRESETS, **_read(PRESETS_FILE)}


def save_preset(name: str, overrides: dict[str, Any]) -> None:
    with _lock:
        allowed = set(Settings.model_fields) - SECRET_FIELDS
        custom = _read(PRESETS_FILE)
        custom[name] = {k: v for k, v in overrides.items() if k in allowed}
        DATA.mkdir(parents=True, exist_ok=True)
        PRESETS_FILE.write_text(json.dumps(custom, indent=2))


def delete_preset(name: str) -> None:
    with _lock:
        custom = _read(PRESETS_FILE)
        custom.pop(name, None)
        PRESETS_FILE.write_text(json.dumps(custom, indent=2))


def resolve(preset: str | None = None, overrides: dict[str, Any] | None = None) -> Settings:
    base = load().model_dump()
    base.update(presets().get(preset or "autopilot", {}))
    base.update({k: v for k, v in (overrides or {}).items() if k in base and k not in SECRET_FIELDS})
    return Settings(**base)


def public(s: Settings) -> dict[str, Any]:
    d = s.model_dump()
    for k in SECRET_FIELDS:
        d[k] = "••••" if s.secret(k) else ""
    return d
