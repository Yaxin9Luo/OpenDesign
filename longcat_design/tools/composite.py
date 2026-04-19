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
from .html_renderer import write_html, write_landing_html
from ..schema import ArtifactType, CompositionArtifacts, ToolObservation
from ..util.logging import log


def composite(args: dict[str, Any], *, ctx: ToolContext) -> ToolObservation:
    spec = ctx.state.get("design_spec")
    if spec is None:
        return obs_error("propose_design_spec must be called first")

    # Landing mode (v1.0 #8) is HTML-only — no PSD/SVG, no per-layer PNGs.
    # It reads the section tree directly from design_spec.layer_graph.
    if spec.artifact_type == ArtifactType.LANDING:
        return _composite_landing(spec, ctx)

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


def _composite_landing(spec: Any, ctx: ToolContext) -> ToolObservation:
    """HTML-only landing-mode composite. Reads the section tree from
    design_spec.layer_graph (not ctx.state['rendered_layers'])."""
    layer_graph = list(spec.layer_graph or [])
    if not layer_graph:
        return obs_error(
            "landing design_spec has empty layer_graph — "
            "propose_design_spec with a section tree first"
        )

    html_path = ctx.run_dir / "index.html"
    preview_path = ctx.run_dir / "preview.png"
    canvas = spec.canvas or {}
    cw = int(canvas.get("w_px", 1200))

    # Re-hydrate image children with src_path from rendered_layers before
    # manifest build / HTML write — see _hydrate_landing_image_srcs docstring.
    _hydrate_landing_image_srcs(layer_graph, ctx)

    manifest: list[dict[str, Any]] = []
    for node in layer_graph:
        kind = getattr(node, "kind", None)
        if kind == "section":
            manifest.append({
                "layer_id": node.layer_id,
                "name": node.name,
                "kind": "section",
                "children": [
                    {"layer_id": c.layer_id, "name": c.name, "kind": c.kind,
                     "text": getattr(c, "text", None),
                     "src_path": getattr(c, "src_path", None)}
                    for c in (node.children or [])
                ],
            })
        elif kind == "text":
            manifest.append({
                "layer_id": node.layer_id,
                "name": node.name,
                "kind": "text",
                "text": node.text,
            })
        elif kind == "image":
            manifest.append({
                "layer_id": node.layer_id,
                "name": node.name,
                "kind": "image",
                "src_path": node.src_path,
            })

    try:
        write_landing_html(spec, html_path, ctx)
    except Exception as e:
        return obs_error(f"landing HTML write failed: {e}")

    try:
        _write_landing_preview(spec, preview_path, ctx)
    except Exception as e:
        return obs_error(f"landing preview render failed: {e}")

    artifacts = CompositionArtifacts(
        psd_path=None,
        svg_path=None,
        html_path=str(html_path),
        preview_path=str(preview_path),
        layer_manifest=manifest,
    )
    ctx.state["composition"] = artifacts

    section_ct = sum(1 for n in layer_graph if getattr(n, "kind", None) == "section")
    image_ct = sum(
        1 for sec in layer_graph
        for c in (getattr(sec, "children", None) or [])
        if getattr(c, "kind", None) == "image" and getattr(c, "src_path", None)
    )
    log("composite.landing.done",
        html=str(html_path), preview=str(preview_path),
        sections=section_ct, images=image_ct, top_level=len(layer_graph))

    return obs_ok(
        f"Composed landing page: {section_ct} section(s), {image_ct} image(s) "
        f"→ HTML + preview (width {cw}px, flow layout)",
        artifacts=[str(html_path), str(preview_path)],
        next_actions=["call critique to self-review", "or call finalize"],
    )


def _hydrate_landing_image_srcs(layer_graph: list[Any], ctx: ToolContext) -> None:
    """Copy `src_path` from ctx.state['rendered_layers'] onto matching image
    children in the spec's layer_graph — so write_landing_html's data-URI
    embedding finds a real file.

    The planner typically declares the section tree in propose_design_spec
    with children having the intended `layer_id`, then separately invokes
    generate_image(layer_id=...) which puts the PNG + src_path into
    rendered_layers. Without this hydration step, the children nodes have
    no src_path and the renderer would silently skip them.
    """
    rendered = ctx.state.get("rendered_layers") or {}
    if not rendered:
        return
    for section in layer_graph:
        if getattr(section, "kind", None) != "section":
            continue
        children = list(getattr(section, "children", None) or [])
        changed = False
        new_children: list[Any] = []
        for child in children:
            if getattr(child, "kind", None) != "image":
                new_children.append(child)
                continue
            if getattr(child, "src_path", None):
                new_children.append(child)
                continue  # already has src_path
            rec = rendered.get(getattr(child, "layer_id", None))
            if rec and rec.get("src_path"):
                try:
                    new_child = child.model_copy(update={
                        "src_path": rec["src_path"],
                        "aspect_ratio": rec.get("aspect_ratio") or child.aspect_ratio,
                    })
                    new_children.append(new_child)
                    changed = True
                except Exception:
                    child.src_path = rec["src_path"]
                    new_children.append(child)
            else:
                new_children.append(child)
        if changed:
            section.children = new_children


def _write_landing_preview(spec: Any, out_path: Path, ctx: ToolContext) -> None:
    """Render a simplified preview PNG for a landing page — a stacked
    top-down rasterization of each section's headline + subhead.

    Not pixel-accurate with the HTML; it exists so the trajectory has a
    preview.png for chat UX + critique.
    """
    from PIL import Image, ImageDraw, ImageFont

    canvas = spec.canvas or {}
    w = min(1200, int(canvas.get("w_px", 1200)))
    # Grow vertically with the number of sections so no section gets clipped.
    layer_graph = list(spec.layer_graph or [])
    section_count = max(1, sum(
        1 for n in layer_graph if getattr(n, "kind", None) == "section"
    ))
    h = 280 + 280 * section_count

    img = Image.new("RGB", (w, h), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    def _font(family: str, size: int) -> ImageFont.FreeTypeFont:
        fonts = ctx.settings.fonts
        fname = fonts.get(family) or fonts[ctx.settings.default_text_font]
        return ImageFont.truetype(str(ctx.settings.fonts_dir / fname), size=size)

    y = 80
    x = 64
    for node in layer_graph:
        kind = getattr(node, "kind", None)
        name = (getattr(node, "name", "") or "").lower()
        variant = next(
            (v for v in ("hero", "features", "cta", "footer", "header") if v in name),
            "content",
        )
        # Section banner row
        if kind == "section":
            # Variant stripe
            band_color = {
                "hero": (15, 23, 42),
                "cta": (15, 23, 42),
                "footer": (15, 23, 42),
                "features": (250, 251, 252),
            }.get(variant, (255, 255, 255))
            text_color = (248, 250, 252) if variant in ("hero", "cta", "footer") else (15, 23, 42)
            section_top = y - 20
            draw.rectangle([(0, section_top), (w, section_top + 220)], fill=band_color)
            # Section tag
            tag = f"§ {variant.upper()}"
            tag_font = _font(ctx.settings.default_text_font, 14)
            draw.text((x, section_top + 12), tag,
                      fill=(248, 250, 252, 200) if variant in ("hero", "cta", "footer") else (148, 163, 184),
                      font=tag_font)
            inner_y = section_top + 52
            for child in (getattr(node, "children", None) or []):
                if getattr(child, "kind", None) != "text":
                    continue
                text = (getattr(child, "text", "") or "")[:80]
                raw_size = int(getattr(child, "font_size_px", None) or 40)
                size = max(16, min(48, raw_size // 2))  # downscale for preview
                fam = getattr(child, "font_family", None) or ctx.settings.default_text_font
                try:
                    f = _font(fam, size)
                except Exception:
                    f = _font(ctx.settings.default_text_font, size)
                draw.text((x, inner_y), text, fill=text_color, font=f)
                inner_y += size + 12
                if inner_y > section_top + 200:
                    break
            y = section_top + 240
        elif kind == "text":
            text = (getattr(node, "text", "") or "")[:80]
            size = max(20, min(60, int(getattr(node, "font_size_px", None) or 48) // 2))
            try:
                f = _font(getattr(node, "font_family", None) or ctx.settings.default_text_font, size)
            except Exception:
                f = _font(ctx.settings.default_text_font, size)
            draw.text((x, y), text, fill=(15, 23, 42), font=f)
            y += size + 20

    img.save(out_path, format="PNG", optimize=True)


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
