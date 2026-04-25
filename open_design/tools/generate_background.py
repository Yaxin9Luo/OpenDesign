"""generate_background — provider-neutral text-to-image wrapper for poster
backgrounds (v2.5 — was Gemini-only through v2.4).

Hard guarantee: appends a no-text directive to every prompt regardless of what
the planner sent, since the SDK has no native negative_prompt and our entire
pipeline assumes background rasters carry zero text.

The actual provider (Gemini / OpenRouter+Seedream / etc) is resolved by
`image_backend.make_image_backend(settings)` from `IMAGE_MODEL` +
`IMAGE_PROVIDER` env vars. This file knows nothing about Gemini or OpenRouter.
"""

from __future__ import annotations

from typing import Any

from ._contract import ToolContext, obs_error, obs_ok
from ..image_backend import ImageGenerationError, make_image_backend
from ..schema import ToolResultRecord
from ..util.io import sha256_file
from ..util.logging import log


NO_TEXT_SUFFIX = (
    "No text, no characters, no lettering, no symbols, no logos, no watermarks."
)


def _ensure_no_text(prompt: str) -> str:
    if NO_TEXT_SUFFIX.lower() in prompt.lower():
        return prompt
    sep = "" if prompt.rstrip().endswith(".") else "."
    return f"{prompt.rstrip()}{sep} {NO_TEXT_SUFFIX}"


def generate_background(args: dict[str, Any], *, ctx: ToolContext) -> ToolResultRecord:
    layer_id = args["layer_id"]
    raw_prompt = args["prompt"]
    aspect_ratio = args.get("aspect_ratio", "3:4")
    image_size = args.get("image_size", "2K")
    safe_zones = args.get("safe_zones", [])

    prompt = _ensure_no_text(raw_prompt)

    prior = ctx.state["rendered_layers"].get(layer_id) or {}
    prior_sha = prior.get("sha256")
    version = ctx.next_layer_version(layer_id)
    out_path = ctx.layers_dir / f"bg_{layer_id}.v{version}.png"

    backend = make_image_backend(ctx.settings)
    log("nbp.request", model=ctx.settings.image_model, provider=backend.name,
        aspect_ratio=aspect_ratio, image_size=image_size, prompt_len=len(prompt))

    try:
        result = backend.generate(
            prompt=prompt,
            aspect_ratio=aspect_ratio,
            image_size=image_size,
        )
    except ImageGenerationError as e:
        return obs_error(str(e), category=e.category)
    except Exception as e:
        return obs_error(
            f"Image backend ({backend.name}/{ctx.settings.image_model}) error: {e}",
            category="api",
        )

    out_path.write_bytes(result.data)
    sha = sha256_file(out_path)
    ctx.state["rendered_layers"][layer_id] = {
        "layer_id": layer_id,
        "name": "background",
        "kind": "background",
        "z_index": 0,
        "bbox": _full_canvas_bbox(ctx),
        "src_path": str(out_path),
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "image_size": image_size,
        "safe_zones": safe_zones,
        "sha256": sha,
        "version": version,
    }
    log("nbp.saved", path=str(out_path), sha=sha[:12], version=version,
        provider=backend.name, model=ctx.settings.image_model)

    payload: dict[str, Any] = {
        "layer_id": layer_id,
        "sha256": sha,
        "width": result.width,
        "height": result.height,
        "relative_path": f"layers/bg_{layer_id}.v{version}.png",
        "version": version,
    }
    if prior_sha:
        payload["supersedes_sha256"] = prior_sha
    return obs_ok(payload)


def _full_canvas_bbox(ctx: ToolContext) -> dict[str, int]:
    spec = ctx.state.get("design_spec")
    if spec is None:
        return {"x": 0, "y": 0, "w": 0, "h": 0}
    canvas = spec.canvas
    return {"x": 0, "y": 0, "w": int(canvas["w_px"]), "h": int(canvas["h_px"])}
