"""NIM-backed AsyncOpenAI client + structured output helpers.

Rate limiter: sliding-window 40 RPM (configurable via NIM_RATE_LIMIT_RPM env var).
All LLM calls route through _rate_limit() — one place, one fix.
"""

from __future__ import annotations
import asyncio
import json
import logging
import time
from collections import deque
from openai import AsyncOpenAI
from pydantic import BaseModel
from app.config import get_settings

logger = logging.getLogger(__name__)

# ── Rate limiter (sliding window) ─────────────────────────────────────────────
_timestamps: deque[float] = deque()
_lock = asyncio.Lock()


async def _rate_limit() -> None:
    """Block until sending another request stays within NIM_RATE_LIMIT_RPM."""
    rpm = get_settings().nim_rate_limit_rpm
    async with _lock:
        now = time.monotonic()
        # Drop timestamps older than 60 s
        while _timestamps and now - _timestamps[0] >= 60.0:
            _timestamps.popleft()
        if len(_timestamps) >= rpm:
            sleep_for = 60.0 - (now - _timestamps[0]) + 0.05  # tiny buffer
            logger.debug("rate_limit: sleeping %.2fs (rpm=%d)", sleep_for, rpm)
            await asyncio.sleep(sleep_for)
            now = time.monotonic()
            while _timestamps and now - _timestamps[0] >= 60.0:
                _timestamps.popleft()
        _timestamps.append(time.monotonic())


# ── Client ────────────────────────────────────────────────────────────────────

def get_client() -> AsyncOpenAI:
    s = get_settings()
    return AsyncOpenAI(api_key=s.nvidia_api_key, base_url=s.nim_base_url)


def _model() -> str:
    return get_settings().nim_model


# ── Public async functions ────────────────────────────────────────────────────

async def structured_call(
    messages: list[dict],
    schema: type[BaseModel],
    temperature: float = 0.1,
) -> BaseModel:
    """Call NIM with JSON output and parse into schema."""
    props = schema.model_json_schema().get("properties", {})
    keys_desc = []
    for k, v in props.items():
        vtype = v.get("type") or v.get("anyOf") or "string"
        desc = v.get("description", "")
        keys_desc.append(f'  "{k}": <{vtype}>' + (f' // {desc}' if desc else ""))
    
    fields_guide = "{\n" + ",\n".join(keys_desc) + "\n}"
    prompt_suffix = (
        f"\n\nIMPORTANT: Respond ONLY with a valid JSON object matching this structure (fill concrete values, DO NOT return schema definitions or 'properties'):\n{fields_guide}"
    )

    if messages and messages[-1]["role"] == "user":
        augmented = messages[:-1] + [{
            "role": "user",
            "content": messages[-1]["content"] + prompt_suffix,
        }]
    else:
        augmented = messages + [{
            "role": "user",
            "content": prompt_suffix,
        }]

    await _rate_limit()
    resp = await get_client().chat.completions.create(
        model=_model(),
        messages=augmented,
        response_format={"type": "json_object"},
        temperature=temperature,
    )
    raw = resp.choices[0].message.content or "{}"
    logger.debug("structured_call raw: %s", raw[:200])

    try:
        data = json.loads(raw)
        # If model returned schema metadata wrapper like {"properties": {...}}, extract defaults or values
        if "properties" in data and isinstance(data["properties"], dict) and not any(k in data for k in props if k != "properties"):
            extracted = {}
            for k in props:
                if k in data["properties"]:
                    prop_info = data["properties"][k]
                    if isinstance(prop_info, dict):
                        extracted[k] = prop_info.get("default") or prop_info.get("value")
                    else:
                        extracted[k] = prop_info
            data = extracted
        return schema.model_validate(data)
    except Exception:
        return schema.model_validate_json(raw)


async def tool_call(
    messages: list[dict],
    tools: list[dict],
    temperature: float = 0.2,
) -> dict:
    """Standard tool-calling request. Returns the raw choice dict."""
    await _rate_limit()
    resp = await get_client().chat.completions.create(
        model=_model(),
        messages=messages,
        tools=tools,
        tool_choice="auto",
        temperature=temperature,
    )
    choice = resp.choices[0]
    return {
        "role": choice.message.role,
        "content": choice.message.content,
        "tool_calls": [
            {
                "id": tc.id,
                "type": tc.type,
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in (choice.message.tool_calls or [])
        ],
    }


async def plain_call(messages: list[dict], temperature: float = 0.3) -> str:
    """Plain text completion."""
    await _rate_limit()
    resp = await get_client().chat.completions.create(
        model=_model(),
        messages=messages,
        temperature=temperature,
    )
    return resp.choices[0].message.content or ""
