"""Claude calls: structured JSON output, optional web search, spend tracking."""
from __future__ import annotations

import json
import logging
from typing import Any

import anthropic

from . import db
from .settings import Settings

log = logging.getLogger(__name__)

# USD per million tokens (input, output)
PRICES = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}
WEB_SEARCH_USD = 0.01
# Models that accept the server-side refusal fallback ("default" routing)
FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}


class LLMError(RuntimeError):
    pass


def _client(s: Settings) -> anthropic.Anthropic:
    key = s.secret("anthropic_api_key")
    return anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()


def _request_kwargs(s: Settings) -> dict[str, Any]:
    kw: dict[str, Any] = {
        "model": s.llm_model,
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": s.llm_effort},
    }
    if s.llm_model in FALLBACK_MODELS:
        kw["extra_headers"] = {"anthropic-beta": "server-side-fallback-2026-07-01"}
        kw["extra_body"] = {"fallbacks": "default"}
    return kw


def _charge(s: Settings, resp: Any, job_id: int | None, what: str) -> None:
    pin, pout = PRICES.get(resp.model, PRICES.get(s.llm_model, (5.0, 25.0)))
    u = resp.usage
    tokens_in = (u.input_tokens or 0) + (getattr(u, "cache_creation_input_tokens", 0) or 0) * 1.25 \
        + (getattr(u, "cache_read_input_tokens", 0) or 0) * 0.1
    usd = tokens_in * pin / 1e6 + (u.output_tokens or 0) * pout / 1e6
    stu = getattr(u, "server_tool_use", None)
    if stu is not None:
        usd += (getattr(stu, "web_search_requests", 0) or 0) * WEB_SEARCH_USD
    db.record_spend(job_id, "anthropic", usd, what)


def _check(resp: Any) -> None:
    if resp.stop_reason == "refusal":
        cat = getattr(getattr(resp, "stop_details", None), "category", None)
        raise LLMError(f"Claude declined this request (category: {cat}). Try a different topic.")
    if resp.stop_reason == "max_tokens":
        raise LLMError("Claude's response was cut off (max_tokens).")


def structured(s: Settings, system: str, content: Any, schema: dict, *,
               job_id: int | None = None, what: str = "") -> dict:
    """One call that returns JSON matching `schema`."""
    if s.llm_provider == "featherless":
        from . import openai_compat
        try:
            return openai_compat.structured(s, system, content, schema)
        except openai_compat.CompatError as e:
            raise LLMError(str(e)) from e
    client = _client(s)
    with client.messages.stream(
        max_tokens=32000,
        system=system,
        messages=[{"role": "user", "content": content}],
        **{**_request_kwargs(s), "output_config": {
            "effort": s.llm_effort,
            "format": {"type": "json_schema", "schema": _strip_defaults(schema)},
        }},
    ) as stream:
        resp = stream.get_final_message()
    _charge(s, resp, job_id, what)
    _check(resp)
    text = "".join(b.text for b in resp.content if b.type == "text")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMError(f"Claude returned invalid JSON: {e}") from e


def research(s: Settings, system: str, prompt: str, *, max_searches: int = 5,
             job_id: int | None = None, what: str = "research") -> str:
    """Free-text answer with Claude's web search tool available."""
    if s.llm_provider != "claude":
        return ""  # no hosted web search on other providers; YouTube API titles still feed refills
    client = _client(s)
    messages: list[dict] = [{"role": "user", "content": prompt}]
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": max_searches}]
    for _ in range(4):  # resume on pause_turn
        with client.messages.stream(max_tokens=16000, system=system, messages=messages,
                                    tools=tools, **_request_kwargs(s)) as stream:
            resp = stream.get_final_message()
        _charge(s, resp, job_id, what)
        _check(resp)
        if resp.stop_reason != "pause_turn":
            return "".join(b.text for b in resp.content if b.type == "text")
        messages.append({"role": "assistant", "content": resp.content})
    return ""


def _strip_defaults(schema: Any) -> Any:
    """`default` is only a hint for lenient providers; keep it out of Claude's strict schema."""
    if isinstance(schema, dict):
        return {k: _strip_defaults(v) for k, v in schema.items() if k != "default"}
    if isinstance(schema, list):
        return [_strip_defaults(v) for v in schema]
    return schema


def obj(props: dict[str, Any]) -> dict:
    """Strict JSON-schema object: every property required, nothing extra."""
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}
