"""Multi-provider image-generation backend (v2.5).

Mirrors the shape of `llm_backend.py` for the same reason: keep tool code
provider-neutral so users can swap image models without touching
`tools/generate_image.py` / `tools/generate_background.py`.

Two backends today:

- `GeminiImageBackend` — wraps `google.genai` (the original NBP path).
  Selected when `image_model` starts with `gemini-` or `imagen-`. Requires
  `GEMINI_API_KEY`.
- `OpenRouterImageBackend` — POSTs to OpenRouter's chat/completions endpoint
  with `modalities=["image","text"]` + `image_config={aspect_ratio,
  image_size}`. Selected for everything else (default model
  `bytedance-seed/seedream-4.5`). Reuses `OPENROUTER_API_KEY`.

Routing rules in `make_image_backend(settings)`:
- `IMAGE_PROVIDER=gemini`     → GeminiImageBackend
- `IMAGE_PROVIDER=openrouter` → OpenRouterImageBackend
- `IMAGE_PROVIDER=auto` (default) → infer from `image_model` prefix:
    `gemini-*` / `imagen-*`  → Gemini
    everything else          → OpenRouter

No silent cross-provider fallback. If the chosen backend fails the tool
returns `obs_error` and the planner sees a real error — same behavior as
v2.4 with the Gemini-only path.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Protocol

from PIL import Image as PILImage


@dataclass(frozen=True)
class ImageResult:
    """Provider-neutral image-generation result.

    `data` is always a PNG byte stream re-encoded through PIL so downstream
    psd-tools / svgwrite / html_renderer can trust the file extension. Width
    and height are read from the decoded PIL image, not the request
    (providers may snap to nearest supported dimension).
    """

    data: bytes
    width: int
    height: int
    mime: str
    model: str


class ImageBackend(Protocol):
    """One method, one shape. Tools call exactly this and never see the
    underlying SDK."""

    name: str
    model: str

    def generate(
        self,
        *,
        prompt: str,
        aspect_ratio: str,
        image_size: str,
    ) -> ImageResult:
        ...


# ──────────────────────────── Gemini ────────────────────────────────


class GeminiImageBackend:
    """Wraps the existing `google.genai` path. Same prompt + config shape
    as v2.4 — this is a refactor, not a behavior change for Gemini users."""

    name = "gemini"

    def __init__(self, settings, model: str):
        from google import genai  # lazy: avoid import unless needed

        self.model = model
        if not getattr(settings, "gemini_api_key", None):
            raise RuntimeError(
                "GeminiImageBackend selected but GEMINI_API_KEY is unset. "
                "Either set GEMINI_API_KEY in .env, or switch IMAGE_MODEL "
                "to a non-Gemini id (e.g. bytedance-seed/seedream-4.5)."
            )
        self._client = genai.Client(api_key=settings.gemini_api_key)

    def generate(
        self,
        *,
        prompt: str,
        aspect_ratio: str,
        image_size: str,
    ) -> ImageResult:
        from google.genai import types

        response = self._client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(
                    aspect_ratio=aspect_ratio,
                    image_size=image_size,
                ),
            ),
        )

        for part in response.parts:
            if part.inline_data:
                return _png_from_bytes(part.inline_data.data, model=self.model)

        raise ImageGenerationError(
            "Gemini returned no image part — likely safety filter or empty response.",
            category="safety_filter",
        )


# ────────────────────────── OpenRouter ──────────────────────────────


class OpenRouterImageBackend:
    """Routes through OpenRouter's chat/completions endpoint with
    `modalities=["image","text"]`. Used for seedream + any other image model
    listed under https://openrouter.ai/models?modality=image.

    The response shape (per docs) is
        choices[0].message.images[i].image_url.url == "data:image/png;base64,..."
    The OpenAI Python SDK doesn't type the `images` field, so we read it
    via `.model_dump()`.
    """

    name = "openrouter"

    def __init__(self, settings, model: str):
        from openai import OpenAI  # lazy

        self.model = model
        # Reuse the same OPENROUTER_API_KEY plumbing as `LLMBackend`. The
        # `OPENAI_COMPAT_*` overrides also work here so users can point at
        # a self-hosted Volcengine ARK gateway, vLLM image bridge, etc.
        base_url = (
            getattr(settings, "openai_compat_base_url", None)
            or "https://openrouter.ai/api/v1"
        )
        api_key = (
            getattr(settings, "openai_compat_api_key", None)
            or getattr(settings, "openrouter_api_key", None)
            or settings.anthropic_api_key  # OR-mode reuses this slot
        )
        if not api_key:
            raise RuntimeError(
                "OpenRouterImageBackend selected but no API key found. "
                "Set OPENROUTER_API_KEY (or OPENAI_COMPAT_API_KEY for a "
                "custom endpoint) in .env."
            )
        self._client = OpenAI(base_url=base_url, api_key=api_key)

    def generate(
        self,
        *,
        prompt: str,
        aspect_ratio: str,
        image_size: str,
    ) -> ImageResult:
        # The OpenAI SDK doesn't model `modalities` / `image_config`
        # natively — they go through `extra_body`, which OpenRouter
        # forwards verbatim to the upstream image model.
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            extra_body={
                "modalities": ["image", "text"],
                "image_config": {
                    "aspect_ratio": aspect_ratio,
                    "image_size": image_size,
                },
            },
        )

        # Non-standard `images` field lives in model_extra; access via dump.
        msg = resp.choices[0].message.model_dump()
        images = msg.get("images") or []
        if not images:
            # Some providers stream images inside content parts; check there too.
            content = msg.get("content")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "image_url":
                        url = (part.get("image_url") or {}).get("url")
                        if url:
                            return _png_from_data_url(url, model=self.model)
            raise ImageGenerationError(
                f"{self.model} via OpenRouter returned no image — likely safety "
                f"filter or unsupported model id. Raw message: {msg!r}",
                category="safety_filter",
            )

        url = (images[0].get("image_url") or {}).get("url")
        if not url:
            raise ImageGenerationError(
                f"{self.model} returned an images entry with no image_url.url: {images[0]!r}",
                category="api",
            )
        return _png_from_data_url(url, model=self.model)


# ─────────────────────────── Factory ────────────────────────────────


def make_image_backend(settings) -> ImageBackend:
    """Resolve `(image_provider, image_model)` to a concrete backend.

    Auto-detection mirrors `LLMBackend`: model id prefix wins when the
    user leaves provider on `auto`.
    """

    provider = (getattr(settings, "image_provider", None) or "auto").lower()
    model = settings.image_model

    if provider == "auto":
        provider = _infer_image_provider(model)

    if provider == "gemini":
        return GeminiImageBackend(settings, model)
    if provider == "openrouter":
        return OpenRouterImageBackend(settings, model)

    raise ValueError(
        f"Unknown IMAGE_PROVIDER={provider!r}. Use auto | gemini | openrouter."
    )


def _infer_image_provider(model: str) -> str:
    m = (model or "").lower()
    if m.startswith("gemini-") or m.startswith("imagen-") or m.startswith("models/gemini"):
        return "gemini"
    return "openrouter"


# ─────────────────────────── Errors ─────────────────────────────────


class ImageGenerationError(RuntimeError):
    """Raised by backends on provider-side failures. Tools catch this and
    convert to `obs_error(message, category=...)` so the planner sees a
    typed failure instead of an opaque traceback.
    """

    def __init__(self, message: str, *, category: str = "api"):
        super().__init__(message)
        self.category = category


# ─────────────────────────── Helpers ────────────────────────────────


def _png_from_data_url(url: str, *, model: str) -> ImageResult:
    """Parse a `data:image/...;base64,XYZ` URL, decode, and re-encode as PNG."""
    if not url.startswith("data:"):
        raise ImageGenerationError(
            f"Expected base64 data URL, got remote URL fetch unsupported: {url[:80]}",
            category="api",
        )
    try:
        _header, payload = url.split(",", 1)
    except ValueError as e:
        raise ImageGenerationError(f"Malformed data URL: {e}", category="api")
    raw = base64.b64decode(payload)
    return _png_from_bytes(raw, model=model)


def _png_from_bytes(raw: bytes, *, model: str) -> ImageResult:
    """Re-encode arbitrary image bytes (JPEG/WebP/PNG) to PNG via PIL.
    Centralizes the v0 invariant that on-disk extensions match the bytes."""
    pil = PILImage.open(BytesIO(raw))
    if pil.mode != "RGB":
        pil = pil.convert("RGB")
    buf = BytesIO()
    pil.save(buf, format="PNG", optimize=True)
    return ImageResult(
        data=buf.getvalue(),
        width=pil.width,
        height=pil.height,
        mime="image/png",
        model=model,
    )
