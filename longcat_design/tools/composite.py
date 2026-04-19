"""composite — bundle layers into PSD + SVG + HTML + flattened preview.

PSD = psd-tools 1.11+ PixelLayers (text layers cropped to bbox for size).
SVG = svgwrite with embedded background + real <text> vector elements.
      Fonts subsetted (only used glyphs) and embedded as base64 WOFF2 in @font-face.
HTML = tools.html_renderer — absolute-positioned poster with contenteditable
       text layers, inlined fonts + images (v1.0 #6).
Preview = PIL alpha_composite chain over an RGB white base.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import svgwrite
from PIL import Image
from psd_tools import PSDImage
from psd_tools.constants import BlendMode, Compression

from ._contract import ToolContext, obs_error, obs_ok
from ._font_embed import build_font_face_css
from .html_renderer import write_html
from ..schema import CompositionArtifacts, ToolObservation
from ..util.logging import log


def composite(args: dict[str, Any], *, ctx: ToolContext) -> ToolObservation:
    spec = ctx.state.get("design_spec")
    if spec is None:
        return obs_error("propose_design_spec must be called first")

    rendered = ctx.state["rendered_layers"]
    if not rendered:
        return obs_error("no layers rendered yet — call generate_background and render_text_layer first")

    canvas = spec.canvas
    cw, ch = int(canvas["w_px"]), int(canvas["h_px"])

    sorted_layers = sorted(rendered.values(), key=lambda L: int(L.get("z_index", 0)))

    psd_path = ctx.run_dir / "poster.psd"
    svg_path = ctx.run_dir / "poster.svg"
    html_path = ctx.run_dir / "poster.html"
    preview_path = ctx.run_dir / "preview.png"

    layer_manifest: list[dict[str, Any]] = []

    try:
        _write_psd(sorted_layers, cw, ch, psd_path, layer_manifest)
    except Exception as e:
        return obs_error(f"PSD write failed: {e}")

    try:
        _write_svg(sorted_layers, cw, ch, svg_path, ctx)
    except Exception as e:
        return obs_error(f"SVG write failed: {e}")

    try:
        write_html(sorted_layers, cw, ch, html_path, ctx)
    except Exception as e:
        return obs_error(f"HTML write failed: {e}")

    try:
        _write_preview(sorted_layers, cw, ch, preview_path)
    except Exception as e:
        return obs_error(f"preview render failed: {e}")

    artifacts = CompositionArtifacts(
        psd_path=str(psd_path),
        svg_path=str(svg_path),
        html_path=str(html_path),
        preview_path=str(preview_path),
        layer_manifest=layer_manifest,
    )
    ctx.state["composition"] = artifacts
    log("composite.done",
        psd=str(psd_path), svg=str(svg_path), html=str(html_path),
        preview=str(preview_path), layers=len(sorted_layers))

    return obs_ok(
        f"Composed {len(sorted_layers)} layers into PSD + SVG + HTML + preview "
        f"({cw}×{ch}px)",
        artifacts=[str(psd_path), str(svg_path), str(html_path), str(preview_path)],
        next_actions=["call critique to self-review", "or call finalize"],
    )


def _write_psd(layers: list[dict[str, Any]], cw: int, ch: int,
               out_path: Path, manifest: list[dict[str, Any]]) -> None:
    psd = PSDImage.new(mode="RGB", size=(cw, ch), depth=8)

    text_group = None

    for L in layers:
        png = Image.open(L["src_path"])
        bbox = L["bbox"]
        bx, by, bw, bh = bbox["x"], bbox["y"], bbox["w"], bbox["h"]

        if L["kind"] == "background":
            if png.mode != "RGB":
                png = png.convert("RGB")
            if png.size != (cw, ch):
                png = png.resize((cw, ch), Image.LANCZOS)
            psd.create_pixel_layer(
                png, name=L["name"], top=0, left=0,
                opacity=255, blend_mode=BlendMode.NORMAL,
                compression=Compression.RLE,
            )
            manifest.append({
                "layer_id": L["layer_id"], "name": L["name"], "kind": "background",
                "png_path": L["src_path"], "bbox": {"x": 0, "y": 0, "w": cw, "h": ch},
            })
        else:
            if png.mode != "RGBA":
                png = png.convert("RGBA")
            crop = png.crop((bx, by, bx + bw, by + bh))
            if text_group is None:
                text_group = psd.create_group(name="text", open_folder=True)
            layer = psd.create_pixel_layer(
                crop, name=L["name"], top=by, left=bx,
                opacity=255, blend_mode=BlendMode.NORMAL,
                compression=Compression.RLE,
            )
            text_group.append(layer)
            manifest.append({
                "layer_id": L["layer_id"], "name": L["name"], "kind": L["kind"],
                "png_path": L["src_path"], "bbox": {"x": bx, "y": by, "w": bw, "h": bh},
            })

    psd.save(str(out_path))


def _write_svg(layers: list[dict[str, Any]], cw: int, ch: int,
               out_path: Path, ctx: ToolContext) -> None:
    text_layers = [L for L in layers if L["kind"] == "text" and L.get("text")]
    bg_layers = [L for L in layers if L["kind"] == "background"]

    fonts_used: dict[str, set[str]] = {}
    for L in text_layers:
        family = L.get("font_family") or ctx.settings.default_text_font
        fonts_used.setdefault(family, set()).update(L["text"])

    font_face_css = build_font_face_css(fonts_used, ctx)

    dwg = svgwrite.Drawing(str(out_path), size=(cw, ch))
    dwg.viewbox(0, 0, cw, ch)

    if font_face_css:
        style = dwg.style(content=font_face_css)
        defs = dwg.defs
        defs.add(style)

    for L in bg_layers:
        with open(L["src_path"], "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        dwg.add(dwg.image(
            href=f"data:image/png;base64,{b64}",
            insert=(0, 0), size=(cw, ch),
        ))

    for L in text_layers:
        bbox = L["bbox"]
        bx, by, bw, bh = bbox["x"], bbox["y"], bbox["w"], bbox["h"]
        font_size = int(L["font_size_px"])
        align = L.get("align") or "left"
        anchor = {"left": "start", "center": "middle", "right": "end"}[align]
        if align == "center":
            tx = bx + bw // 2
        elif align == "right":
            tx = bx + bw
        else:
            tx = bx
        ty = by + font_size  # top-of-em ≈ baseline shifted down by font_size

        family = L.get("font_family") or ctx.settings.default_text_font
        attrs = {
            "insert": (tx, ty),
            "font_family": f"'{family}'",
            "font_size": font_size,
            "fill": L.get("fill", "#000000"),
            "text_anchor": anchor,
        }
        effects = L.get("effects") or {}
        stroke = effects.get("stroke") or {}
        if stroke.get("width", 0):
            attrs["stroke"] = stroke.get("color", "#000000")
            attrs["stroke_width"] = int(stroke["width"])
        dwg.add(dwg.text(L["text"], **attrs))

    dwg.save(pretty=True)


def _write_preview(layers: list[dict[str, Any]], cw: int, ch: int, out_path: Path) -> None:
    base = Image.new("RGBA", (cw, ch), (255, 255, 255, 255))
    for L in layers:
        png = Image.open(L["src_path"])
        if png.mode != "RGBA":
            png = png.convert("RGBA")
        if L["kind"] == "background":
            if png.size != (cw, ch):
                png = png.resize((cw, ch), Image.LANCZOS)
            base = Image.alpha_composite(base, png)
        else:
            if png.size != (cw, ch):
                full = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
                full.paste(png, (0, 0))
                png = full
            base = Image.alpha_composite(base, png)
    base.convert("RGB").save(out_path, format="PNG", optimize=True)
