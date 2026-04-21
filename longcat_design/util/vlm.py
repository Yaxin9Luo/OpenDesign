"""Neutral VLM dispatcher for `tools/ingest_document` (v1.2+).

Why this exists
---------------
The project's planner and critic stay 100 % on the Anthropic SDK
(OpenRouter's Anthropic-compatible endpoint keeps the tool_use
protocol identical — see docs/DECISIONS.md for the reasoning behind
not mixing in the OpenAI SDK there). Ingest is different: it is a
stateless single-turn "read this document" call, with no tool_use, and
Qwen-VL-Max on OpenRouter is the cost/speed sweet spot for that
workload. Qwen is served only via OpenRouter's OpenAI-compatible
endpoint, so ingest needs a second SDK path.

Scope: THIS MODULE IS INGEST-ONLY. Do not import it from planner.py or
critic.py — doing so would silently downgrade tool_use support.

Public surface
--------------
- `vlm_call_json(...)` — one-shot JSON-producing request with one or
  more attached images. Picks the SDK based on `model`:
    * `qwen/*`, any non-Anthropic id → openai.OpenAI against OpenRouter.
    * `claude-*`, `anthropic/*`      → anthropic.Anthropic against whichever
                                        base_url `settings` dictates.
  Returns the parsed JSON dict. Raises `RuntimeError` on non-JSON output.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from anthropic import Anthropic
from openai import OpenAI

from .logging import log


# Known VLM providers keyed by OpenRouter model id. Unknown ids fall
# back to prefix inference (see `_provider_for_model`).
_VLM_PROVIDERS: dict[str, str] = {
    "qwen/qwen-vl-max": "openai",
    "qwen/qwen-vl-plus": "openai",
    "qwen/qwen2.5-vl-72b-instruct": "openai",
    "qwen/qwen2.5-vl-32b-instruct": "openai",
}

# Anthropic-SDK endpoint for stock Anthropic when `anthropic_base_url`
# is None. OpenAI SDK (for Qwen) always hits OpenRouter v1.
_OPENROUTER_OPENAI_BASE = "https://openrouter.ai/api/v1"


_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


# ────────────────────────── data types ────────────────────────────────

@dataclass(frozen=True)
class VlmImage:
    """One image attachment for a VLM call.

    `data` is the raw bytes on disk; `media_type` is the MIME we report
    to the model (it's fine if the bytes are actually JPEG but we say
    "image/png" — both providers sniff).
    """
    data: bytes
    media_type: str = "image/png"

    @classmethod
    def from_path(cls, path: Path) -> "VlmImage":
        mt = "image/jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else "image/png"
        return cls(data=path.read_bytes(), media_type=mt)


# ────────────────────────── public API ────────────────────────────────

def vlm_call_json(
    *,
    settings,  # `config.Settings` — typed loosely to avoid import cycle
    model: str,
    system: str,
    user_text: str,
    images: list[VlmImage] | None = None,
    max_tokens: int = 4096,
    timeout_s: float | None = None,
) -> dict[str, Any]:
    """Single-turn "describe these images as JSON" request.

    Routes to the Anthropic SDK or OpenAI SDK based on `model`. The
    prompt is expected to ask for a fenced ```json ...``` block; we
    parse that out and return the dict. Raises `RuntimeError` on
    non-JSON output.
    """
    images = images or []
    provider = _provider_for_model(model)
    timeout_s = timeout_s or getattr(settings, "ingest_http_timeout", 600.0)

    log("vlm.request", model=model, provider=provider,
        n_images=len(images), timeout_s=timeout_s)

    if provider == "anthropic":
        text = _call_anthropic(
            settings=settings, model=model, system=system,
            user_text=user_text, images=images,
            max_tokens=max_tokens, timeout_s=timeout_s,
        )
    else:
        text = _call_openai(
            settings=settings, model=model, system=system,
            user_text=user_text, images=images,
            max_tokens=max_tokens, timeout_s=timeout_s,
        )

    return _parse_json_block(text)


# ────────────────────────── internals ─────────────────────────────────

def _provider_for_model(model: str) -> str:
    if model in _VLM_PROVIDERS:
        return _VLM_PROVIDERS[model]
    if model.startswith(("claude-", "anthropic/")):
        return "anthropic"
    # Unknown → assume OpenRouter/OpenAI-compatible.
    return "openai"


def _call_anthropic(
    *,
    settings,
    model: str,
    system: str,
    user_text: str,
    images: list[VlmImage],
    max_tokens: int,
    timeout_s: float,
) -> str:
    kwargs: dict[str, Any] = {
        "api_key": settings.anthropic_api_key,
        "timeout": timeout_s,
        "max_retries": 1,
    }
    if settings.anthropic_base_url:
        kwargs["base_url"] = settings.anthropic_base_url
    client = Anthropic(**kwargs)

    content: list[dict[str, Any]] = []
    for img in images:
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": img.media_type,
                "data": base64.standard_b64encode(img.data).decode("ascii"),
            },
        })
    content.append({"type": "text", "text": user_text})

    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": content}],
    )
    parts = [getattr(b, "text", "") for b in resp.content
             if getattr(b, "type", None) == "text"]
    return "".join(parts)


def _call_openai(
    *,
    settings,
    model: str,
    system: str,
    user_text: str,
    images: list[VlmImage],
    max_tokens: int,
    timeout_s: float,
) -> str:
    # Qwen on OpenRouter uses the OpenRouter API key. We accept either
    # `openrouter_api_key` (preferred name) or reuse `anthropic_api_key`
    # when the project was started in OpenRouter mode (single key holds
    # both roles — see config.load_settings).
    api_key = (
        getattr(settings, "openrouter_api_key", None)
        or settings.anthropic_api_key
    )
    if not api_key:
        raise RuntimeError(
            "VLM openai branch needs an OpenRouter key — set OPENROUTER_API_KEY."
        )

    client = OpenAI(
        api_key=api_key,
        base_url=_OPENROUTER_OPENAI_BASE,
        timeout=timeout_s,
        max_retries=1,
    )

    user_content: list[dict[str, Any]] = []
    for img in images:
        b64 = base64.standard_b64encode(img.data).decode("ascii")
        user_content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{img.media_type};base64,{b64}"},
        })
    user_content.append({"type": "text", "text": user_text})

    resp = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ],
    )
    choice = resp.choices[0] if resp.choices else None
    if not choice or not choice.message or not choice.message.content:
        return ""
    return choice.message.content


def _parse_json_block(text: str) -> dict[str, Any]:
    """Accept a fenced ```json ...``` block OR a raw JSON object."""
    m = _JSON_BLOCK_RE.search(text)
    payload = m.group(1) if m else text.strip()
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"VLM returned non-JSON: {e}; got {text[:300]!r}"
        )
    if not isinstance(value, dict):
        raise RuntimeError(f"VLM returned JSON but not a dict: {type(value).__name__}")
    return value
