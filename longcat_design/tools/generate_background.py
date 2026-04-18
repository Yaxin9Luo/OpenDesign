"""generate_background — Gemini Nano Banana Pro wrapper.

Hard guarantee: appends a no-text directive to every prompt regardless of what
the planner sent, since the SDK has no native negative_prompt and our entire
pipeline assumes background rasters carry zero text.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from google import genai
from google.genai import types

from ._contract import ToolContext, obs_error, obs_ok
from ..schema import ToolObservation
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


def generate_background(args: dict[str, Any], *, ctx: ToolContext) -> ToolObservation:
    layer_id = args["layer_id"]
    raw_prompt = args["prompt"]
    aspect_ratio = args.get("aspect_ratio", "3:4")
    image_size = args.get("image_size", "2K")
    safe_zones = args.get("safe_zones", [])

    prompt = _ensure_no_text(raw_prompt)

    out_path = ctx.layers_dir / f"bg_{layer_id}.png"

    client = genai.Client(api_key=ctx.settings.gemini_api_key)
    log("nbp.request", model=ctx.settings.image_model,
        aspect_ratio=aspect_ratio, image_size=image_size, prompt_len=len(prompt))

    try:
        response = client.models.generate_content(
            model=ctx.settings.image_model,
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
        return obs_error(
            f"Gemini API error: {e}",
            next_actions=["retry generate_background with simpler prompt", "or proceed without background"],
        )

    image_saved = False
    for part in response.parts:
        if part.inline_data:
            # Always re-encode via PIL — Gemini's inline_data is JPEG regardless
            # of the file extension we ask the SDK to save with, and downstream
            # psd-tools / svgwrite expect the extension to match the bytes.
            from io import BytesIO
            from PIL import Image as PILImage
            pil = PILImage.open(BytesIO(part.inline_data.data))
            if pil.mode != "RGB":
                pil = pil.convert("RGB")
            pil.save(out_path, format="PNG", optimize=True)
            image_saved = True
            break

    if not image_saved:
        return obs_error(
            "Gemini returned no image part — likely safety filter or empty response",
            next_actions=["rephrase prompt avoiding sensitive terms"],
        )

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
    }
    log("nbp.saved", path=str(out_path), sha=sha[:12])

    return obs_ok(
        f"Generated background ({aspect_ratio}, {image_size}) → {out_path.name}",
        artifacts=[str(out_path)],
        next_actions=["call render_text_layer for each text element"],
    )


def _full_canvas_bbox(ctx: ToolContext) -> dict[str, int]:
    spec = ctx.state.get("design_spec")
    if spec is None:
        return {"x": 0, "y": 0, "w": 0, "h": 0}
    canvas = spec.canvas
    return {"x": 0, "y": 0, "w": int(canvas["w_px"]), "h": int(canvas["h_px"])}
