"""Multi-provider image-generation backend (v2.5, hardened v2.7.5).

Mirrors the shape of `llm_backend.py` for the same reason: keep tool code
provider-neutral so users can swap image models without touching
`tools/generate_image.py` / `tools/generate_background.py`.

Two concrete backends today:

- `GeminiImageBackend` — wraps `google.genai` (the original NBP path).
  Selected when `image_model` starts with `gemini-` or `imagen-`. Requires
  `GEMINI_API_KEY`.
- `OpenRouterImageBackend` — POSTs to OpenRouter's chat/completions endpoint
  with `modalities=["image","text"]` + `image_config={aspect_ratio,
  image_size}`. Selected for everything else (default model
  `google/gemini-2.5-flash-image`). Reuses `OPENROUTER_API_KEY`.

Plus a wrapper:

- `FallbackImageBackend` (v2.7.5) — wraps a primary backend and an
  optional fallback backend. On `provider_unavailable` failures from
  the primary (404 / no-endpoints-for-modality / model-not-found) it
  transparently retries against the fallback, logging
  `image.fallback.attempt`. All other failure categories (safety_filter,
  api 5xx, malformed responses) propagate from the primary unchanged —
  we only fall back when the user-chosen MODEL is the broken thing.

Routing rules in `make_image_backend(settings)`:
- `IMAGE_PROVIDER=gemini`     → GeminiImageBackend
- `IMAGE_PROVIDER=openrouter` → OpenRouterImageBackend
- `IMAGE_PROVIDER=auto` (default) → infer from `image_model` prefix:
    `gemini-*` / `imagen-*`  → Gemini
    everything else          → OpenRouter

The factory wraps the resolved backend in `FallbackImageBackend` when
`settings.image_fallback_model` is non-empty AND points at a different
model id than the primary; otherwise the bare backend is returned and
behavior matches v2.5.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Protocol

from PIL import Image as PILImage

from .util.logging import log


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

        try:
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
        except Exception as e:
            # v2.7.5 — flag model-not-found / endpoint-unavailable failures
            # so FallbackImageBackend can route around them. Gemini surfaces
            # these as 404 / "Model ... was not found" / "is not supported".
            msg = str(e)
            lowered = msg.lower()
            if (
                "not found" in lowered
                or "is not supported" in lowered
                or "no such model" in lowered
                or "404" in msg
            ):
                raise ImageGenerationError(
                    f"{self.model} via Gemini is unavailable: {msg}",
                    category="provider_unavailable",
                ) from e
            raise ImageGenerationError(
                f"{self.model} via Gemini raised {type(e).__name__}: {msg}",
                category="api",
            ) from e

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
        try:
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
        except Exception as e:
            # v2.7.5 — recognise the OpenRouter "model is broken / not
            # routable for this modality" surface so FallbackImageBackend
            # can detect it categorically. Three known shapes:
            #   - 404 + "No endpoints found that support the requested
            #     output modalities" (Seedream 4.5 since 2026-04-26)
            #   - 404 + "No endpoints found for <model>" (model unlisted)
            #   - 400 + "<model> is not a valid model ID" (typo / dropped)
            msg = str(e)
            lowered = msg.lower()
            if (
                "no endpoints found" in lowered
                or "is not a valid model id" in lowered
                or "model_not_found" in lowered
            ):
                raise ImageGenerationError(
                    f"{self.model} via OpenRouter is unavailable: {msg}",
                    category="provider_unavailable",
                ) from e
            raise ImageGenerationError(
                f"{self.model} via OpenRouter raised {type(e).__name__}: {msg}",
                category="api",
            ) from e

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


# ─────────────────────── Fallback wrapper (v2.7.5) ──────────────────


class FallbackImageBackend:
    """Wraps a primary `ImageBackend` and an optional fallback so a
    single broken model id doesn't take down `generate_image` /
    `generate_background` for the whole run.

    Trigger: ONLY `ImageGenerationError(category="provider_unavailable")`
    from the primary. Every other failure (safety_filter, api, malformed
    response) propagates unchanged — those mean "this prompt / this
    request shape doesn't work", not "this model is the wrong tool".

    The fallback is constructed lazily on first failure so a cold path
    where the primary always works has zero extra import cost.

    Logging: every fallback attempt emits `image.fallback.attempt` with
    `primary_model`, `fallback_model`, `category`, and the truncated
    error message — enough for SFT extractors to find these turns later.
    On fallback success → `image.fallback.success`. On fallback
    failure → re-raise the FALLBACK's error so the tool sees the most
    recent attempt's category (typically still `provider_unavailable`,
    but could be `safety_filter` if the fallback model gates differently).
    """

    name = "fallback"

    def __init__(self, primary: ImageBackend, settings: Any, fallback_model: str):
        self.primary = primary
        self.model = primary.model  # surface the user-chosen id to logs
        self._settings = settings
        self._fallback_model = fallback_model
        self._fallback_backend: ImageBackend | None = None  # lazy

    def _build_fallback(self) -> ImageBackend:
        if self._fallback_backend is not None:
            return self._fallback_backend
        provider = _infer_image_provider(self._fallback_model)
        if provider == "gemini":
            backend = GeminiImageBackend(self._settings, self._fallback_model)
        else:
            backend = OpenRouterImageBackend(self._settings, self._fallback_model)
        self._fallback_backend = backend
        return backend

    def generate(
        self,
        *,
        prompt: str,
        aspect_ratio: str,
        image_size: str,
    ) -> ImageResult:
        try:
            return self.primary.generate(
                prompt=prompt,
                aspect_ratio=aspect_ratio,
                image_size=image_size,
            )
        except ImageGenerationError as e:
            if e.category != "provider_unavailable":
                raise
            log(
                "image.fallback.attempt",
                primary_model=self.primary.model,
                fallback_model=self._fallback_model,
                category=e.category,
                error=str(e)[:240],
            )
            try:
                fb = self._build_fallback()
            except Exception as build_err:
                # If the fallback can't even be constructed (e.g. missing
                # credentials) keep the primary's typed failure and
                # surface the construction error in the message — never
                # mask the original cause.
                raise ImageGenerationError(
                    f"primary {self.primary.model} unavailable AND fallback "
                    f"{self._fallback_model} could not be initialised: "
                    f"{type(build_err).__name__}: {build_err}. "
                    f"Original primary error: {e}",
                    category="provider_unavailable",
                ) from e
            try:
                result = fb.generate(
                    prompt=prompt,
                    aspect_ratio=aspect_ratio,
                    image_size=image_size,
                )
            except ImageGenerationError as fb_err:
                # Both providers down → terminal. Annotate the message
                # with both model ids so the planner's next turn sees an
                # actionable error and can pivot to a paper figure.
                raise ImageGenerationError(
                    f"image generation failed on BOTH primary "
                    f"({self.primary.model}) and fallback "
                    f"({self._fallback_model}). primary={e}; fallback={fb_err}. "
                    f"Set IMAGE_MODEL=<an alternative> in .env, or pivot the "
                    f"slide to use an ingest_fig_NN paper figure instead.",
                    category=fb_err.category,
                ) from fb_err
            log(
                "image.fallback.success",
                primary_model=self.primary.model,
                fallback_model=self._fallback_model,
                width=result.width,
                height=result.height,
            )
            return result


# ─────────────────────────── Factory ────────────────────────────────


def make_image_backend(settings) -> ImageBackend:
    """Resolve `(image_provider, image_model)` to a concrete backend.

    Auto-detection mirrors `LLMBackend`: model id prefix wins when the
    user leaves provider on `auto`. The result is wrapped in
    `FallbackImageBackend` whenever `settings.image_fallback_model` is
    non-empty AND distinct from the primary model — gives v2.7.5+ runs
    transparent recovery from `provider_unavailable` failures (e.g. the
    Seedream 4.5 endpoint loss observed 2026-04-26) without forcing the
    planner to retry the same broken call.
    """

    primary = _build_concrete_backend(settings, settings.image_model)

    fb_model = (getattr(settings, "image_fallback_model", "") or "").strip()
    if fb_model and fb_model != settings.image_model:
        return FallbackImageBackend(primary, settings, fb_model)
    return primary


def _build_concrete_backend(settings, model: str) -> ImageBackend:
    provider = (getattr(settings, "image_provider", None) or "auto").lower()
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
