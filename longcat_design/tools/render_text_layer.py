"""render_text_layer — Pillow text → transparent RGBA PNG sized to full canvas.

Supports stroke and drop-shadow effects. Position uses top-left origin pixel
coords; alignment within bbox is honoured (left/center/right).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ._contract import ToolContext, obs_error, obs_ok, obs_partial
from ..schema import ToolObservation
from ..util.io import sha256_file
from ..util.logging import log


def _resolve_font(font_family: str | None, ctx: ToolContext) -> tuple[Path, str, bool]:
    """Returns (font_path, resolved_family, was_fallback)."""
    fonts = ctx.settings.fonts
    if font_family and font_family in fonts:
        return ctx.settings.fonts_dir / fonts[font_family], font_family, False
    fallback = ctx.settings.default_text_font
    return ctx.settings.fonts_dir / fonts[fallback], fallback, True


def _wrap_lines(text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    """Greedy wrap. Splits Latin on spaces; CJK char-by-char."""
    if not text:
        return [""]
    tokens: list[str] = []
    buf = ""
    for ch in text:
        if ch.isspace():
            if buf:
                tokens.append(buf)
                buf = ""
            tokens.append(ch)
        elif ord(ch) > 0x2E80:  # CJK range start (rough)
            if buf:
                tokens.append(buf)
                buf = ""
            tokens.append(ch)
        else:
            buf += ch
    if buf:
        tokens.append(buf)

    lines: list[str] = []
    cur = ""
    for tok in tokens:
        candidate = cur + tok
        bbox = font.getbbox(candidate)
        w = bbox[2] - bbox[0]
        if w <= max_w or not cur:
            cur = candidate
        else:
            lines.append(cur.rstrip())
            cur = tok if not tok.isspace() else ""
    if cur:
        lines.append(cur.rstrip())
    return lines or [""]


def render_text_layer(args: dict[str, Any], *, ctx: ToolContext) -> ToolObservation:
    spec = ctx.state.get("design_spec")
    if spec is None:
        return obs_error("propose_design_spec must be called first")

    canvas = spec.canvas
    cw, ch = int(canvas["w_px"]), int(canvas["h_px"])

    layer_id = args["layer_id"]
    name = args["name"]
    text = args["text"]
    font_family = args.get("font_family")
    font_size = int(args["font_size_px"])
    fill = args.get("fill", "#000000")
    bbox = args["bbox"]
    align = args.get("align", "left")
    effects = args.get("effects") or {}

    font_path, resolved_family, was_fallback = _resolve_font(font_family, ctx)
    try:
        font = ImageFont.truetype(str(font_path), size=font_size)
    except Exception as e:
        return obs_error(f"font load failed ({font_path}): {e}")

    img = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    bx, by, bw, bh = int(bbox["x"]), int(bbox["y"]), int(bbox["w"]), int(bbox["h"])
    lines = _wrap_lines(text, font, bw)

    line_metrics = [font.getbbox(line) for line in lines]
    line_heights = [m[3] - m[1] for m in line_metrics]
    line_gap = int(font_size * 0.2)
    total_h = sum(line_heights) + line_gap * (len(lines) - 1)
    cy = by + max(0, (bh - total_h) // 2)

    shadow = effects.get("shadow")
    if shadow:
        sh_color = shadow.get("color", "#000000A0")
        sh_dx, sh_dy = int(shadow.get("dx", 0)), int(shadow.get("dy", 4))
        sh_blur = int(shadow.get("blur", 12))
        shadow_img = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        sh_draw = ImageDraw.Draw(shadow_img)
        _y = cy
        for line, m, lh in zip(lines, line_metrics, line_heights):
            line_w = m[2] - m[0]
            x = _line_x(align, bx, bw, line_w)
            sh_draw.text((x + sh_dx, _y + sh_dy), line, font=font, fill=sh_color)
            _y += lh + line_gap
        if sh_blur > 0:
            shadow_img = shadow_img.filter(ImageFilter.GaussianBlur(radius=sh_blur))
        img = Image.alpha_composite(img, shadow_img)
        draw = ImageDraw.Draw(img)

    stroke = effects.get("stroke") or {}
    stroke_width = int(stroke.get("width", 0))
    stroke_fill = stroke.get("color", "#000000")

    _y = cy
    for line, m, lh in zip(lines, line_metrics, line_heights):
        line_w = m[2] - m[0]
        x = _line_x(align, bx, bw, line_w)
        kw = {"font": font, "fill": fill}
        if stroke_width > 0:
            kw["stroke_width"] = stroke_width
            kw["stroke_fill"] = stroke_fill
        draw.text((x, _y), line, **kw)
        _y += lh + line_gap

    out_path = ctx.layers_dir / f"text_{layer_id}.png"
    img.save(out_path, format="PNG", optimize=True)
    sha = sha256_file(out_path)

    ctx.state["rendered_layers"][layer_id] = {
        "layer_id": layer_id,
        "name": name,
        "kind": "text",
        "z_index": int(args.get("z_index", 1)),
        "bbox": {"x": bx, "y": by, "w": bw, "h": bh},
        "text": text,
        "font_family": resolved_family,
        "font_size_px": font_size,
        "fill": fill,
        "align": align,
        "effects": effects,
        "src_path": str(out_path),
        "sha256": sha,
    }
    log("text.rendered", layer=name, font=resolved_family, fallback=was_fallback,
        chars=len(text), path=str(out_path))

    summary = f"Rendered text layer '{name}' ({len(text)} chars, {resolved_family}) → {out_path.name}"
    if was_fallback:
        return obs_partial(
            summary + f" [WARN: font_family '{font_family}' not bundled; fell back to {resolved_family}]",
            artifacts=[str(out_path)],
        )
    return obs_ok(summary, artifacts=[str(out_path)])


def _line_x(align: str, bx: int, bw: int, line_w: int) -> int:
    if align == "center":
        return bx + max(0, (bw - line_w) // 2)
    if align == "right":
        return bx + max(0, bw - line_w)
    return bx
