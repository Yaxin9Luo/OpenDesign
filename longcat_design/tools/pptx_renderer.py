"""PPTX renderer for deck artifacts (v1.0 #7).

One top-level LayerNode with `kind="slide"` per slide; each slide's `children`
hold `kind in {"text","image","background"}` elements positioned by bbox in
slide-canvas pixel coords. We emit native PowerPoint shapes with native
TextFrames — no Pillow rasterization of text — so the .pptx opens
type-editable in PowerPoint / Keynote / Google Slides.

Per-slide simplified PNGs are also written (for chat preview + critic).

Font embedding is intentionally NOT performed: .pptx delegates font rendering
to the consuming app's font engine (see `docs/GOTCHAS.md` for the CJK-on-
consumer-PowerPoint note). This mirrors Paper2Any's approach.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.util import Emu, Pt

from ._contract import ToolContext


# 1 pixel at 96 DPI ≈ 9525 EMU. python-pptx accepts Emu(int).
PX_TO_EMU = 9525

DEFAULT_SLIDE_W = 1920
DEFAULT_SLIDE_H = 1080

_ALIGN_MAP = {
    "left": PP_ALIGN.LEFT,
    "center": PP_ALIGN.CENTER,
    "right": PP_ALIGN.RIGHT,
}


def write_pptx(spec: Any, pptx_path: Path, ctx: ToolContext) -> int:
    """Walk the slide tree and emit a .pptx file. Returns slide count."""
    canvas = spec.canvas or {}
    slide_w = int(canvas.get("w_px") or DEFAULT_SLIDE_W)
    slide_h = int(canvas.get("h_px") or DEFAULT_SLIDE_H)

    prs = Presentation()
    prs.slide_width = Emu(slide_w * PX_TO_EMU)
    prs.slide_height = Emu(slide_h * PX_TO_EMU)

    blank_layout = prs.slide_layouts[6]  # "Blank" layout — we position everything.

    slide_count = 0
    for node in (spec.layer_graph or []):
        if getattr(node, "kind", None) != "slide":
            continue
        slide = prs.slides.add_slide(blank_layout)
        _render_slide(slide, node, slide_w, slide_h, ctx)
        slide_count += 1

    prs.save(str(pptx_path))
    return slide_count


def _render_slide(slide: Any, slide_node: Any, slide_w: int, slide_h: int,
                  ctx: ToolContext) -> None:
    """Add shapes for each child element of a slide LayerNode."""
    children = list(getattr(slide_node, "children", None) or [])
    # Sort by z_index so higher z draws on top (pptx respects insertion order).
    children.sort(key=lambda c: int(getattr(c, "z_index", 0) or 0))

    for child in children:
        kind = getattr(child, "kind", None)
        if kind == "background":
            _add_background(slide, child, slide_w, slide_h)
        elif kind == "image":
            _add_picture(slide, child, slide_w, slide_h)
        elif kind == "text":
            _add_text_frame(slide, child, slide_w, slide_h)
        elif kind == "table":
            _add_table(slide, child, slide_w, slide_h)
        # silently skip unknown kinds; planner enforces vocab

    # v2.3 — populate PowerPoint's notes pane from slide.speaker_notes.
    # `notes_slide` is auto-created on first access by python-pptx; we only
    # write when the planner provided actual text, so slides without notes
    # keep an empty (but valid) notes_slide underneath.
    notes = getattr(slide_node, "speaker_notes", None)
    if notes:
        slide.notes_slide.notes_text_frame.text = notes


def _bbox_to_emu(bbox: Any, slide_w: int, slide_h: int,
                 default_bbox: tuple[int, int, int, int] | None = None
                 ) -> tuple[Emu, Emu, Emu, Emu]:
    """Resolve a bbox (or fallback to slide-sized) to EMU left/top/width/height."""
    if bbox is not None:
        x = int(getattr(bbox, "x", 0) or 0)
        y = int(getattr(bbox, "y", 0) or 0)
        w = int(getattr(bbox, "w", slide_w) or slide_w)
        h = int(getattr(bbox, "h", slide_h) or slide_h)
    elif default_bbox is not None:
        x, y, w, h = default_bbox
    else:
        x, y, w, h = 0, 0, slide_w, slide_h
    # clamp to slide bounds
    x = max(0, min(x, slide_w - 1))
    y = max(0, min(y, slide_h - 1))
    w = max(1, min(w, slide_w - x))
    h = max(1, min(h, slide_h - y))
    return Emu(x * PX_TO_EMU), Emu(y * PX_TO_EMU), Emu(w * PX_TO_EMU), Emu(h * PX_TO_EMU)


def _add_background(slide: Any, node: Any, slide_w: int, slide_h: int) -> None:
    """Full-slide picture background. Planner can pass bbox or leave None to
    cover the whole slide."""
    src = getattr(node, "src_path", None)
    if not src:
        return  # no background image available; PowerPoint default is white
    if not Path(src).exists():
        return
    left, top, width, height = _bbox_to_emu(
        getattr(node, "bbox", None), slide_w, slide_h,
        default_bbox=(0, 0, slide_w, slide_h),
    )
    slide.shapes.add_picture(src, left, top, width=width, height=height)


def _add_picture(slide: Any, node: Any, slide_w: int, slide_h: int) -> None:
    src = getattr(node, "src_path", None)
    if not src or not Path(src).exists():
        return
    left, top, width, height = _bbox_to_emu(
        getattr(node, "bbox", None), slide_w, slide_h,
    )
    slide.shapes.add_picture(src, left, top, width=width, height=height)


def _add_table(slide: Any, node: Any, slide_w: int, slide_h: int) -> None:
    """Render a `kind="table"` layer as a native PowerPoint table.

    Expects `node.rows: list[list[str]]` and optional `node.headers:
    list[str]`. When `headers` is empty, the first row of `rows` is
    promoted to the header. When `rows` is empty we bail out silently
    (planner is expected to not reference empty tables, but defensive).

    Sizing:
    - bbox width/height set the table's outer frame.
    - Row heights are even; header row is slightly taller.
    - Column widths are proportional to the max string length seen in
      that column — rough but prevents one wide column from collapsing
      everything else.
    - Font size auto-shrinks based on row count to keep cells legible
      at deck scale (floor 10pt, ceiling 18pt).

    Optional: `node.col_highlight_rule: list[str]` — per-column "max"
    / "min" / "". When set, the winning row per column is bolded.
    """
    rows = getattr(node, "rows", None) or []
    headers = list(getattr(node, "headers", None) or [])
    col_rule = list(getattr(node, "col_highlight_rule", None) or [])
    if not rows and not headers:
        return

    # Promote first row if headers empty.
    if not headers and rows:
        headers = [str(c) for c in rows[0]]
        rows = rows[1:]

    # Normalize all rows to header width (pad / truncate).
    n_cols = max(len(headers), max((len(r) for r in rows), default=0))
    if n_cols == 0:
        return
    headers = [str(h) for h in headers] + [""] * (n_cols - len(headers))
    headers = headers[:n_cols]
    rows = [
        [str(c) for c in r] + [""] * (n_cols - len(r))
        for r in rows
    ]
    rows = [r[:n_cols] for r in rows]

    # Normalize rule list length.
    if col_rule:
        col_rule = col_rule[:n_cols] + [""] * max(0, n_cols - len(col_rule))

    # Winner map for bold highlighting. Import here to avoid cycles.
    from ..util.table_png import _compute_winner_rows
    winner_rows = _compute_winner_rows(rows, col_rule)

    n_rows = len(rows) + 1  # +1 for header
    left, top, width, height = _bbox_to_emu(
        getattr(node, "bbox", None), slide_w, slide_h,
    )

    shape = slide.shapes.add_table(n_rows, n_cols, left, top, width, height)
    table = shape.table

    # Column widths proportional to max string length in column.
    total_w = sum(col.width for col in table.columns)
    col_weights = []
    for c in range(n_cols):
        cells = [headers[c]] + [row[c] for row in rows]
        max_len = max((len(str(v)) for v in cells), default=1)
        col_weights.append(max(1, min(max_len, 30)))
    total_weight = sum(col_weights)
    for c, col in enumerate(table.columns):
        col.width = Emu(int(total_w * col_weights[c] / total_weight))

    # Font-size autoscale: smaller when the table has many rows.
    body_pt = 18 if n_rows <= 6 else 14 if n_rows <= 12 else 11
    header_pt = body_pt + 1

    _fill_table_row(table, 0, headers, font_pt=header_pt,
                    is_header=True, winner_cols=set())
    for r_idx, row in enumerate(rows, start=1):
        # Data row idx in the rows list is r_idx - 1.
        winning_cols = {c for c, win_r in winner_rows.items()
                        if win_r == r_idx - 1}
        _fill_table_row(table, r_idx, row, font_pt=body_pt,
                        is_header=False, winner_cols=winning_cols)


def _fill_table_row(table: Any, r: int, values: list[str],
                    *, font_pt: int, is_header: bool,
                    winner_cols: set[int] | None = None) -> None:
    winner_cols = winner_cols or set()
    for c, val in enumerate(values):
        cell = table.cell(r, c)
        cell.text = ""  # clear default
        tf = cell.text_frame
        para = tf.paragraphs[0]
        para.alignment = PP_ALIGN.CENTER if is_header else PP_ALIGN.LEFT
        run = para.add_run()
        run.text = val
        font = run.font
        font.size = Pt(font_pt)
        # Bold for header row OR for the winning data cell per column.
        font.bold = is_header or (c in winner_cols)
        if is_header:
            font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
            # Fill header cell with a dark accent (python-pptx solid-fill API).
            from pptx.oxml.ns import qn
            tcPr = cell._tc.get_or_add_tcPr()
            for existing in tcPr.findall(qn("a:solidFill")):
                tcPr.remove(existing)
            from lxml import etree
            fill = etree.SubElement(tcPr, qn("a:solidFill"))
            etree.SubElement(fill, qn("a:srgbClr"), val="1F2A44")


def _add_text_frame(slide: Any, node: Any, slide_w: int, slide_h: int) -> None:
    text = (getattr(node, "text", None) or "").strip()
    if not text:
        return
    left, top, width, height = _bbox_to_emu(
        getattr(node, "bbox", None), slide_w, slide_h,
    )
    shape = slide.shapes.add_textbox(left, top, width, height)
    tf = shape.text_frame
    tf.word_wrap = True

    # First paragraph holds the initial run; subsequent lines become new paras.
    lines = text.splitlines() or [text]
    for i, line in enumerate(lines):
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        para.text = ""  # clear any default run
        align = getattr(node, "align", None)
        if align in _ALIGN_MAP:
            para.alignment = _ALIGN_MAP[align]
        run = para.add_run()
        run.text = line
        font = run.font
        # python-pptx wants pts; we get px. pt ≈ px * 72/96 = px * 0.75.
        size_px = int(getattr(node, "font_size_px", None) or 36)
        font.size = Pt(max(6, round(size_px * 0.75)))
        family = getattr(node, "font_family", None)
        if family:
            font.name = family
        effects = getattr(node, "effects", None)
        fill_hex = None
        if effects is not None:
            fill_hex = getattr(effects, "fill", None)
        if fill_hex and isinstance(fill_hex, str) and fill_hex.startswith("#"):
            rgb = _hex_to_rgb(fill_hex)
            if rgb is not None:
                font.color.rgb = RGBColor(*rgb)


def _hex_to_rgb(hx: str) -> tuple[int, int, int] | None:
    s = hx.lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        return None
    try:
        return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    except ValueError:
        return None


def render_slide_preview_png(slide_node: Any, slide_w: int, slide_h: int,
                             out_path: Path, ctx: ToolContext) -> None:
    """Pillow-render a simplified preview of one slide.

    Not pixel-accurate with PowerPoint's renderer — it's an at-a-glance thumb
    for chat UX + for stitching into the grid preview. Shows the bg image (if
    any), image shapes (as resized thumbs), and text (approximated font).
    """
    # Downscale to a chat-friendly size while preserving aspect.
    max_w = 960
    scale = min(1.0, max_w / slide_w)
    w = max(1, int(slide_w * scale))
    h = max(1, int(slide_h * scale))

    img = Image.new("RGB", (w, h), (255, 255, 255))

    children = sorted(
        list(getattr(slide_node, "children", None) or []),
        key=lambda c: int(getattr(c, "z_index", 0) or 0),
    )

    for child in children:
        kind = getattr(child, "kind", None)
        if kind in ("background", "image", "table"):
            # Tables use their pre-rendered src_path (PIL-drawn PNG) —
            # ingest_document baked it. PPTX itself holds a live table
            # shape; this preview just needs a raster for the thumbnail.
            _paste_image(img, child, slide_w, slide_h, scale)
        elif kind == "text":
            _draw_text(img, child, slide_w, slide_h, scale, ctx)

    img.save(out_path, format="PNG", optimize=True)


def _scaled_bbox(bbox: Any, slide_w: int, slide_h: int,
                 scale: float,
                 default_full: bool = False) -> tuple[int, int, int, int]:
    if bbox is not None:
        x = int(getattr(bbox, "x", 0) or 0)
        y = int(getattr(bbox, "y", 0) or 0)
        w = int(getattr(bbox, "w", slide_w) or slide_w)
        h = int(getattr(bbox, "h", slide_h) or slide_h)
    elif default_full:
        x, y, w, h = 0, 0, slide_w, slide_h
    else:
        x, y, w, h = 0, 0, slide_w // 2, slide_h // 4
    return (
        max(0, int(x * scale)),
        max(0, int(y * scale)),
        max(1, int(w * scale)),
        max(1, int(h * scale)),
    )


def _paste_image(canvas: Image.Image, node: Any, slide_w: int, slide_h: int,
                 scale: float) -> None:
    src = getattr(node, "src_path", None)
    if not src or not Path(src).exists():
        return
    try:
        tile = Image.open(src).convert("RGBA")
    except Exception:
        return
    default_full = getattr(node, "kind", None) == "background"
    sx, sy, sw, sh = _scaled_bbox(
        getattr(node, "bbox", None), slide_w, slide_h, scale,
        default_full=default_full,
    )
    tile = tile.resize((sw, sh), Image.LANCZOS)
    canvas.alpha_composite(tile, dest=(sx, sy)) if canvas.mode == "RGBA" else canvas.paste(tile, (sx, sy), tile)


def _draw_text(canvas: Image.Image, node: Any, slide_w: int, slide_h: int,
               scale: float, ctx: ToolContext) -> None:
    text = (getattr(node, "text", None) or "").strip()
    if not text:
        return
    sx, sy, sw, sh = _scaled_bbox(
        getattr(node, "bbox", None), slide_w, slide_h, scale,
        default_full=False,
    )

    # Pick a reasonable approximated font size from the node metadata.
    size_px = int(getattr(node, "font_size_px", None) or 36)
    approx = max(10, min(120, int(size_px * scale)))
    family = getattr(node, "font_family", None) or ctx.settings.default_text_font

    fonts = ctx.settings.fonts
    fname = fonts.get(family) or fonts[ctx.settings.default_text_font]
    try:
        font = ImageFont.truetype(str(ctx.settings.fonts_dir / fname), size=approx)
    except Exception:
        font = ImageFont.load_default()

    effects = getattr(node, "effects", None)
    fill_hex = getattr(effects, "fill", None) if effects is not None else None
    rgb = _hex_to_rgb(fill_hex) if isinstance(fill_hex, str) else None
    fill = rgb or (15, 23, 42)

    draw = ImageDraw.Draw(canvas)
    # Word-wrap to bbox: simple char-by-char wrap (works for CJK; latin uses spaces).
    lines = _wrap_for_width(text, font, sw, draw)
    line_h = approx + 6
    y = sy
    for line in lines:
        if y + line_h > sy + sh:
            break
        draw.text((sx, y), line, fill=fill, font=font)
        y += line_h


def _wrap_for_width(text: str, font: Any, max_w: int, draw: Any) -> list[str]:
    """Simple word/char wrap to fit a pixel width."""
    out: list[str] = []
    for paragraph in text.splitlines() or [text]:
        if " " in paragraph:
            # space-delimited: greedy word-wrap
            words = paragraph.split()
            line = ""
            for w in words:
                probe = (line + " " + w).strip()
                if _measure(probe, font, draw) <= max_w:
                    line = probe
                else:
                    if line:
                        out.append(line)
                    line = w
            if line:
                out.append(line)
        else:
            # CJK-style: char-by-char
            line = ""
            for ch in paragraph:
                probe = line + ch
                if _measure(probe, font, draw) <= max_w:
                    line = probe
                else:
                    if line:
                        out.append(line)
                    line = ch
            if line:
                out.append(line)
    return out


def _measure(s: str, font: Any, draw: Any) -> int:
    try:
        bbox = draw.textbbox((0, 0), s, font=font)
        return int(bbox[2] - bbox[0])
    except Exception:
        return len(s) * 10
