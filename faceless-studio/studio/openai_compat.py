"""Writer backend for OpenAI-compatible chat APIs (Featherless by default).

Open models don't guarantee schema-valid JSON, so the schema goes in the
prompt, the reply is cleaned (thinking tags, code fences, surrounding prose),
small gaps are filled from the schema, and one repair round-trip is tried
before giving up. Featherless is a flat subscription, so no spend is logged.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

from . import net
from .settings import Settings


class CompatError(RuntimeError):
    pass


def _convert(content: Any) -> tuple[Any, bool]:
    """Anthropic-style content blocks -> OpenAI message content. Returns (content, has_images)."""
    if isinstance(content, str):
        return content, False
    parts, images = [], False
    for block in content:
        if block.get("type") == "text":
            parts.append({"type": "text", "text": block["text"]})
        elif block.get("type") == "image":
            src = block["source"]
            parts.append({"type": "image_url",
                          "image_url": {"url": f"data:{src['media_type']};base64,{src['data']}"}})
            images = True
    return parts, images


def extract_json(text: str) -> Any:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I)
    text = re.sub(r"^.*?</think>", "", text, flags=re.S | re.I)  # unopened thinking block
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in reply")
    raw = text[start:end + 1]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return json.loads(re.sub(r",\s*([}\]])", r"\1", raw))  # trailing commas


_DEFAULTS = {"string": "", "integer": 0, "number": 0, "boolean": False, "array": [], "object": {}}


def coerce(schema: dict, value: Any, path: str = "$") -> Any:
    """Fill missing fields and fix simple type slips; raise on structural problems."""
    t = schema.get("type")
    if t == "object":
        if not isinstance(value, dict):
            raise ValueError(f"{path} should be an object")
        out = {}
        for k, sub in schema.get("properties", {}).items():
            out[k] = coerce(sub, value[k], f"{path}.{k}") if k in value else \
                coerce(sub, sub.get("default", _DEFAULTS.get(sub.get("type"))), f"{path}.{k}")
        return out
    if t == "array":
        if isinstance(value, str) and schema.get("items", {}).get("type") == "string":
            value = [v.strip().lstrip("#") for v in re.split(r"[,\s]+", value) if v.strip()]
        if not isinstance(value, list):
            raise ValueError(f"{path} should be a list")
        return [coerce(schema.get("items", {}), v, f"{path}[{i}]") for i, v in enumerate(value)]
    if "enum" in schema:
        return value if value in schema["enum"] else schema["enum"][0]
    try:
        if t == "integer":
            return int(float(value or 0))
        if t == "number":
            return float(value or 0)
        if t == "boolean":
            return value if isinstance(value, bool) else str(value).lower() in ("true", "1", "yes")
        if t == "string":
            return "" if value is None else str(value)
    except (TypeError, ValueError):
        return _DEFAULTS[t]
    return value


def _post(s: Settings, payload: dict) -> str:
    key = s.secret("featherless_api_key")
    if not key:
        raise CompatError("Featherless selected but no API key set (Settings → API keys)")
    url = s.featherless_base_url.rstrip("/") + "/chat/completions"
    last = None
    for attempt in range(4):
        try:
            with net.client(600) as c:
                r = c.post(url, headers={"Authorization": f"Bearer {key}"}, json=payload)
            if r.status_code == 429 or r.status_code >= 500:
                last = f"{r.status_code}: {r.text[:300]}"
                time.sleep(5 * (attempt + 1))
                continue
            if r.status_code >= 400:
                raise CompatError(f"Featherless error {r.status_code}: {r.text[:500]}")
            msg = r.json()["choices"][0]["message"]
            return msg.get("content") or ""
        except httpx.HTTPError as e:
            last = str(e)
            time.sleep(5 * (attempt + 1))
    raise CompatError(f"Featherless request failed after retries: {last}")


def structured(s: Settings, system: str, content: Any, schema: dict) -> dict:
    user, images = _convert(content)
    model = s.featherless_vision_model if images else s.featherless_model
    sys_prompt = (f"{system}\n\nReply with ONLY a JSON object that matches this JSON schema. "
                  f"No prose, no markdown, no code fences.\n{json.dumps(schema)}")
    messages = [{"role": "system", "content": sys_prompt}, {"role": "user", "content": user}]
    for attempt in range(2):
        reply = _post(s, {"model": model, "messages": messages, "max_tokens": 8000, "temperature": 0.8})
        try:
            return coerce(schema, extract_json(reply))
        except (ValueError, json.JSONDecodeError) as e:
            if attempt:
                raise CompatError(f"{model} didn't return usable JSON: {e}") from e
            messages += [{"role": "assistant", "content": reply},
                         {"role": "user", "content": f"That was not valid ({e}). Reply again with only the corrected JSON object."}]
    raise CompatError("unreachable")
