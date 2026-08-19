"""LLM gateway — the only place that knows which model provider is in use.

Agents ask for a *capability* ("classification", "reasoning", ...) and get a
structured object back. They never import a provider SDK, so swapping NVIDIA NIM
for OpenAI is a change here and nowhere else.

When no key is configured `available()` is False and every caller falls back to
its deterministic path — the system must work without a model, not merely
degrade politely.
"""

from __future__ import annotations

import json
import os
from typing import Any, Literal, TypeVar

from dotenv import load_dotenv

from pydantic import BaseModel, ValidationError

load_dotenv()

Capability = Literal[
    "classification", "structured_completion", "reasoning", "summarization", "long_context"
]

# Capability -> model. Cheap and fast for classification, stronger for planning.
def _model(env: str, default: str) -> str:
    return os.getenv(env) or os.getenv("NIM_MODEL") or default


MODELS: dict[Capability, str] = {
    "classification": _model("LLM_MODEL_FAST", "meta/llama-3.1-8b-instruct"),
    "structured_completion": _model("LLM_MODEL_FAST", "meta/llama-3.1-8b-instruct"),
    "reasoning": _model("LLM_MODEL_REASONING", "meta/llama-3.3-70b-instruct"),
    "summarization": _model("LLM_MODEL_FAST", "meta/llama-3.1-8b-instruct"),
    "long_context": _model("LLM_MODEL_LONG", "meta/llama-3.3-70b-instruct"),
}

T = TypeVar("T", bound=BaseModel)


class LLMUnavailable(RuntimeError):
    """No provider is configured, or the configured one refused the request."""


def api_key() -> str | None:
    return os.getenv("NVIDIA_API_KEY") or os.getenv("OPENAI_API_KEY") or None


def base_url() -> str:
    return (
        os.getenv("LLM_BASE_URL")
        or os.getenv("NIM_BASE_URL")
        or "https://integrate.api.nvidia.com/v1"
    )


def available() -> bool:
    return bool(api_key())


# A provider that hangs must not hang the caller. The OpenAI SDK defaults to a
# 600s timeout with retries, which stalls a whole eval run on one bad request.
REQUEST_TIMEOUT = float(os.getenv("LLM_TIMEOUT_SECONDS", "60"))
MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "1"))


def _client() -> Any:
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - depends on install extras
        raise LLMUnavailable("openai SDK not installed") from exc
    key = api_key()
    if not key:
        raise LLMUnavailable("no NVIDIA_API_KEY or OPENAI_API_KEY configured")
    return OpenAI(
        api_key=key,
        base_url=base_url(),
        timeout=REQUEST_TIMEOUT,
        max_retries=MAX_RETRIES,
    )


_last_call_time: float = 0.0


def _pace_request() -> None:
    global _last_call_time
    try:
        rpm = float(os.getenv("NIM_RATE_LIMIT_RPM", "40"))
    except ValueError:
        rpm = 40.0
    interval = (60.0 / rpm) + 0.05
    import time
    elapsed = time.time() - _last_call_time
    if elapsed < interval:
        time.sleep(interval - elapsed)
    _last_call_time = time.time()


def _clean_json_str(raw: str) -> str:
    """Strip markdown blocks or extra surrounding noise if present."""
    text = (raw or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 2 and lines[0].startswith("```") and lines[-1].startswith("```"):
            text = "\n".join(lines[1:-1]).strip()
        elif lines[0].startswith("```"):
            text = "\n".join(lines[1:]).strip()
    # If there is leading/trailing text outside the outermost braces:
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    return text


def complete_structured(
    schema: type[T],
    system: str,
    user: str,
    *,
    capability: Capability = "structured_completion",
    temperature: float = 0.0,
    example: BaseModel | dict[str, Any] | None = None,
) -> T:
    """Return an instance of `schema`, or raise LLMUnavailable."""
    import time

    client = _client()
    shape = (
        example.model_dump(mode="json") if isinstance(example, BaseModel) else example
    )
    prompt = f"{user}\n\nRespond with a valid JSON object only matching the required data structure."
    if shape is not None:
        prompt += f"\nExample JSON structure:\n{json.dumps(shape)}"
    else:
        prompt += f"\nRequired JSON Schema:\n{json.dumps(schema.model_json_schema())}"

    max_retries = 3
    retries = 0
    content = ""
    while True:
        _pace_request()
        try:
            response = client.chat.completions.create(
                model=MODELS[capability],
                messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                temperature=temperature,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content or ""
            break
        except Exception as exc:
            retries += 1
            if retries > max_retries:
                raise LLMUnavailable(f"{type(exc).__name__}: {exc}") from exc
            time.sleep(1.0 * (2 ** (retries - 1)))

    cleaned = _clean_json_str(content)

    # 1. Direct validation
    try:
        return schema.model_validate_json(cleaned)
    except ValidationError:
        pass

    # 2. Try parsing dict and checking nested keys
    try:
        data = json.loads(cleaned)
    except Exception as exc:
        raise LLMUnavailable(f"model returned non-JSON content: {cleaned[:200]}") from exc

    if isinstance(data, dict):
        try:
            return schema.model_validate(data)
        except ValidationError:
            # Check if wrapped in a single key like "understanding", "result", "data"
            for k, v in data.items():
                if isinstance(v, dict):
                    try:
                        return schema.model_validate(v)
                    except ValidationError:
                        pass
                elif isinstance(v, list) and hasattr(schema, "model_fields") and len(schema.model_fields) == 1:
                    try:
                        field_name = next(iter(schema.model_fields.keys()))
                        return schema.model_validate({field_name: v})
                    except ValidationError:
                        pass

    # If all normalization attempts fail, raise LLMUnavailable with clear error
    try:
        return schema.model_validate(data if isinstance(data, dict) else {})
    except ValidationError as exc:
        raise LLMUnavailable(f"model returned an invalid {schema.__name__}: {exc}") from exc
