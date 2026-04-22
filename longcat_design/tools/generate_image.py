"""generate_image — NBP wrapper for inline landing-section images (v1.0 #8.75).

Semantically distinct from `generate_background`:
- `generate_background` = one full-canvas raster behind EVERYTHING in a poster.
  Always text-free. Has `safe_zones` for text overlay regions.
- `generate_image` = an inline image that sits inside a landing `<section>`
  (hero product shot, feature-card illustration, etc.). No safe_zones.
  Still required to be text-free — the renderer emits any real text as
  separate contenteditable HTML.

The planner appends this layer under a section node's `children` list so
the HTML renderer can emit `<img>` alongside text flow. No PSD/SVG output
for landing — the image is inlined into the final HTML as a `data:` URI by
composite → html_renderer → write_landing_html.
"""

from __future__ import annotations

from io import BytesIO
from typing import Any

from google import genai
from google.genai import types
from PIL import Image as PILImage

from ._contract import ToolContext, obs_error, obs_ok
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


def generate_image(args: dict[str, Any], *, ctx: ToolContext) -> ToolResultRecord:
    layer_id = args["layer_id"]
    raw_prompt = args["prompt"]
    aspect_ratio = args.get("aspect_ratio", "16:9")
    image_size = args.get("image_size", "1K")
    name = args.get("name", layer_id)

    prompt = _ensure_no_text(raw_prompt)
    out_path = ctx.layers_dir / f"img_{layer_id}.png"

    client = genai.Client(api_key=ctx.settings.gemini_api_key)
    log("nbp.image.request", model=ctx.settings.image_model,
        aspect_ratio=aspect_ratio, image_size=image_size,
        prompt_len=len(prompt), layer_id=layer_id)

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
        return obs_error(f"Gemini API error on generate_image: {e}", category="api")

    image_saved = False
    img_w = img_h = 0
    for part in response.parts:
        if part.inline_data:
            # Gemini inline_data is JPEG regardless of requested extension;
            # always re-encode through PIL.
            pil = PILImage.open(BytesIO(part.inline_data.data))
            if pil.mode != "RGB":
                pil = pil.convert("RGB")
            pil.save(out_path, format="PNG", optimize=True)
            img_w, img_h = pil.size
            image_saved = True
            break

    if not image_saved:
        return obs_error(
            "Gemini returned no image part — likely safety filter or empty response",
            category="safety_filter",
        )

    sha = sha256_file(out_path)
    ctx.state["rendered_layers"][layer_id] = {
        "layer_id": layer_id,
        "name": name,
        "kind": "image",
        "z_index": int(args.get("z_index", 1)),
        "bbox": None,                # flow layout — no pixel bbox
        "src_path": str(out_path),
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "image_size": image_size,
        "sha256": sha,
    }
    log("nbp.image.saved", path=str(out_path), sha=sha[:12], layer_id=layer_id)

    return obs_ok({
        "layer_id": layer_id,
        "sha256": sha,
        "width": img_w,
        "height": img_h,
    })
