"""Generate the three canonical architecture diagrams for OpenDesign.

We draw them with Pillow primitives (no matplotlib / graphviz dep) because
(a) Pillow is already in the runtime deps, (b) the diagrams are simple
boxes + arrows + text and PIL gives tight pixel control, and (c) the
bundled NotoSansSC-Bold / NotoSerifSC-Bold fonts guarantee consistent
rendering on any dev box.

Outputs:
  assets/diagrams/agent_architecture.png   — planner + 11 tools + outputs
  assets/diagrams/rendering_pipeline.png   — DesignSpec → 3 artifact types
  assets/diagrams/paper2any_flow.png       — ingest dispatch + VLM + layer registry

Run:
  uv run python scripts/make_diagrams.py

Re-run whenever the docs/architecture drift enough that the visual needs
updating. The PNGs themselves are committed so repo consumers (README /
landing / slides) can reference them without re-running this script.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


REPO_ROOT = Path(__file__).resolve().parents[1]
FONTS_DIR = REPO_ROOT / "assets" / "fonts"
OUT_DIR = REPO_ROOT / "assets" / "diagrams"

SANS = str(FONTS_DIR / "NotoSansSC-Bold.otf")
SERIF = str(FONTS_DIR / "NotoSerifSC-Bold.otf")

# Editorial palette — restrained cream + ink with a single deep-red accent.
BG = (251, 249, 244)            # cream
INK = (26, 26, 26)              # near-black
INK_MUTED = (107, 99, 88)
RULE = (42, 40, 36)
ACCENT = (122, 32, 32)          # deep red
FILL_SOFT = (242, 237, 225)     # light card fill
FILL_ACCENT_SOFT = (250, 232, 228)
SHADOW = (0, 0, 0, 22)          # very soft


@dataclass(frozen=True)
class Box:
    x: int
    y: int
    w: int
    h: int
    title: str
    subtitle: str = ""
    fill: tuple = FILL_SOFT
    border: tuple = RULE
    border_w: int = 2
    title_pt: int = 26
    subtitle_pt: int = 16
    title_color: tuple = INK
    subtitle_color: tuple = INK_MUTED

    @property
    def cx(self) -> int:
        return self.x + self.w // 2

    @property
    def cy(self) -> int:
        return self.y + self.h // 2

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size=size)


def _draw_box(d: ImageDraw.ImageDraw, b: Box) -> None:
    d.rectangle(
        [b.x, b.y, b.right, b.bottom],
        fill=b.fill,
        outline=b.border,
        width=b.border_w,
    )
    title_f = _font(SANS, b.title_pt)
    title_w = d.textlength(b.title, font=title_f)
    if b.subtitle:
        sub_f = _font(SANS, b.subtitle_pt)
        # Multi-line subtitles are allowed via `\n`; account for each line
        # in the vertical block so the whole title+subtitle stays centered.
        lines = b.subtitle.split("\n")
        sub_line_h = b.subtitle_pt + 4
        total_h = b.title_pt + 8 + len(lines) * sub_line_h - 4
        y0 = b.cy - total_h // 2
        d.text(
            (b.cx - title_w // 2, y0),
            b.title, font=title_f, fill=b.title_color,
        )
        line_y = y0 + b.title_pt + 8
        for line in lines:
            lw = d.textlength(line, font=sub_f)
            d.text(
                (b.cx - lw // 2, line_y),
                line, font=sub_f, fill=b.subtitle_color,
            )
            line_y += sub_line_h
    else:
        th = b.title_pt
        d.text(
            (b.cx - title_w // 2, b.cy - th // 2),
            b.title, font=title_f, fill=b.title_color,
        )


def _arrow(d: ImageDraw.ImageDraw,
           x1: int, y1: int, x2: int, y2: int,
           color: tuple = RULE, width: int = 2,
           head_len: int = 14, head_w: int = 9) -> None:
    """Draw a straight arrow from (x1,y1) to (x2,y2) with a filled triangular head."""
    d.line([x1, y1, x2, y2], fill=color, width=width)
    # Head — compute a unit vector along the line + a perpendicular for the
    # base edge. Purely 2D: avoids a numpy dep for three diagrams.
    import math
    dx, dy = x2 - x1, y2 - y1
    length = max(1.0, math.hypot(dx, dy))
    ux, uy = dx / length, dy / length
    # Base of the triangle sits head_len before the tip.
    bx, by = x2 - ux * head_len, y2 - uy * head_len
    # Perpendicular unit vector (rotate 90°).
    px, py = -uy, ux
    p1 = (bx + px * head_w, by + py * head_w)
    p2 = (bx - px * head_w, by - py * head_w)
    d.polygon([(x2, y2), p1, p2], fill=color)


def _label(d: ImageDraw.ImageDraw, x: int, y: int, text: str,
           *, pt: int = 15, color: tuple = INK_MUTED,
           center: bool = False, serif: bool = False) -> None:
    font_path = SERIF if serif else SANS
    f = _font(font_path, pt)
    if center:
        w = d.textlength(text, font=f)
        d.text((x - w // 2, y), text, font=f, fill=color)
    else:
        d.text((x, y), text, font=f, fill=color)


def _title(d: ImageDraw.ImageDraw, cw: int, title: str, subtitle: str,
           y: int = 36) -> None:
    f = _font(SERIF, 44)
    tw = d.textlength(title, font=f)
    d.text((cw // 2 - tw // 2, y), title, font=f, fill=INK)
    # accent underline rule
    d.line([cw // 2 - 40, y + 60, cw // 2 + 40, y + 60], fill=ACCENT, width=3)
    sf = _font(SANS, 18)
    sw = d.textlength(subtitle, font=sf)
    d.text((cw // 2 - sw // 2, y + 80), subtitle, font=sf, fill=INK_MUTED)


def _footer(d: ImageDraw.ImageDraw, cw: int, ch: int, text: str) -> None:
    f = _font(SANS, 14)
    w = d.textlength(text, font=f)
    d.text((cw // 2 - w // 2, ch - 30), text, font=f, fill=INK_MUTED)


# ─────────────────────── Diagram 1: agent architecture ──────────────────


def diagram_agent_architecture(out_path: Path) -> None:
    """Three horizontal bands, left-to-right + top-to-bottom:
      1. Entry column  (User → ChatREPL → PipelineRunner)
      2. Core loop     (PlannerLoop ↔ Critic) + bracketed tool catalog
      3. Output band   (POSTER / LANDING / DECK)
    Persistence (Trajectory JSON / ChatSession JSON) is intentionally
    omitted — the diagram reads cleaner without the diagonal sidechannel
    and persistence is covered in docs/ARCHITECTURE.md prose.
    """
    W, H = 2200, 1260
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)

    _title(d, W, "OpenDesign · Agent Architecture",
           "LLMBackend abstraction (Kimi K2.6 default · Claude via env var) · handwritten tool loop · no LangGraph / CrewAI")

    # ── Band 1: entry column (left) ──────────────────────────────────────
    user = Box(100, 220, 260, 72, "User", "CLI terminal", fill=FILL_ACCENT_SOFT)
    chat = Box(100, 320, 260, 72, "ChatREPL", "chat.py · 8 slash cmds")
    runner = Box(100, 420, 260, 92,
                 "PipelineRunner",
                 "runner.py\nper-turn orchestration",
                 title_pt=22, subtitle_pt=14)
    for b in (user, chat, runner):
        _draw_box(d, b)
    _arrow(d, user.cx, user.bottom, chat.cx, chat.y)
    _arrow(d, chat.cx, chat.bottom, runner.cx, runner.y)

    # ── Band 2: planner + critic (center) ────────────────────────────────
    planner = Box(520, 280, 380, 110,
                  "PlannerLoop",
                  "planner.py · LLMBackend\n(Kimi K2.6 / Claude / any OpenAI-compat)",
                  fill=FILL_ACCENT_SOFT,
                  title_pt=28, subtitle_pt=15)
    critic = Box(520, 440, 380, 80,
                 "Critic", "critic.py · pass / revise / fail",
                 title_pt=24, subtitle_pt=14)
    for b in (planner, critic):
        _draw_box(d, b)
    _arrow(d, runner.right, runner.cy, planner.x, planner.cy)
    # Planner ↔ critic feedback loop (two vertical arrows between the cards)
    _arrow(d, planner.cx - 60, planner.bottom, critic.cx - 60, critic.y)
    _arrow(d, critic.cx + 60, critic.y, planner.cx + 60, planner.bottom,
           color=ACCENT)
    _label(d, (planner.cx + critic.cx) // 2 + 76, planner.bottom + 12,
           "revise", pt=14, color=ACCENT)

    # ── Band 2 right: 11-tool catalog, bracketed to planner ──────────────
    tool_x0 = 1080
    tool_y0 = 220
    tool_names = [
        ("switch_artifact_type", "poster / deck / landing"),
        ("ingest_document", "v1.2 paper2any"),
        ("propose_design_spec", "blueprint JSON"),
        ("generate_background", "NBP — poster"),
        ("generate_image", "NBP — landing / deck"),
        ("render_text_layer", "Pillow rasterize"),
        ("edit_layer", "targeted diff"),
        ("fetch_brand_asset", "v1 stub"),
        ("composite", "PSD+SVG+HTML | HTML | PPTX"),
        ("critique", "self-review"),
        ("finalize", "serialize DistillTrajectory"),
    ]
    card_w, card_h = 480, 62
    col_gap, row_gap = 24, 16
    tool_boxes: list[Box] = []
    for i, (name, sub) in enumerate(tool_names):
        row = i // 2
        col = i % 2
        x = tool_x0 + col * (card_w + col_gap)
        y = tool_y0 + row * (card_h + row_gap)
        fill = FILL_ACCENT_SOFT if name == "composite" else FILL_SOFT
        b = Box(x, y, card_w, card_h, name, sub,
                fill=fill, title_pt=20, subtitle_pt=13)
        _draw_box(d, b)
        tool_boxes.append(b)

    bundle_x = tool_x0 - 36
    bundle_y_top = tool_boxes[0].y
    bundle_y_bot = tool_boxes[-1].bottom
    d.line([bundle_x, bundle_y_top, bundle_x, bundle_y_bot],
           fill=RULE, width=2)
    d.line([bundle_x - 16, bundle_y_top, bundle_x, bundle_y_top],
           fill=RULE, width=2)
    d.line([bundle_x - 16, bundle_y_bot, bundle_x, bundle_y_bot],
           fill=RULE, width=2)
    _arrow(d, planner.right, planner.cy, bundle_x, planner.cy)
    _label(d, bundle_x + 10, planner.cy - 42, "tool_use × N",
           pt=14, color=INK_MUTED)

    # ── Band 3: composite → artifact outputs ─────────────────────────────
    # Route the dispatch rail around the tool grid (NOT down through
    # `finalize` which sits right under composite). Exit composite on the
    # LEFT edge, drop to the rail band, then fan out to the three outputs.
    composite_box = tool_boxes[8]
    rail_y = tool_boxes[-1].bottom + 90
    # L-shape: left from composite, down to rail band
    rail_left_x = composite_box.x - 40
    d.line([composite_box.x, composite_box.cy,
            rail_left_x, composite_box.cy], fill=RULE, width=2)
    d.line([rail_left_x, composite_box.cy,
            rail_left_x, rail_y], fill=RULE, width=2)
    rail_right_x = W - 200
    d.line([rail_left_x, rail_y, rail_right_x, rail_y],
           fill=RULE, width=2)
    _label(d, rail_left_x + 12, composite_box.cy - 36,
           "dispatches on  spec.artifact_type",
           pt=14, color=INK_MUTED)

    out_y = rail_y + 30
    out_w, out_h = 620, 170
    outputs = [
        ("POSTER", "poster.psd · poster.svg\nposter.html · preview.png", FILL_SOFT),
        ("LANDING", "index.html (6 design systems)\n+ CTA · nav · reveal  (v1.3)",
         FILL_ACCENT_SOFT),
        ("DECK", "deck.pptx (native TextFrames)\n+ slides/slide_NN.png", FILL_SOFT),
    ]
    out_start_x = (W - (3 * out_w + 2 * 40)) // 2
    out_boxes: list[Box] = []
    for i, (title, sub, fill) in enumerate(outputs):
        x = out_start_x + i * (out_w + 40)
        b = Box(x, out_y, out_w, out_h, title, sub, fill=fill,
                title_pt=32, subtitle_pt=16)
        _draw_box(d, b)
        out_boxes.append(b)
        _arrow(d, b.cx, rail_y, b.cx, b.y)

    _footer(d, W, H, "Source of truth: planner.py + runner.py + llm_backend.py · 11 tools in tools/__init__.py · v2 DistillTrajectory schema")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    im.save(out_path, "PNG", optimize=True)


# ───────────────── Diagram 2: 3-artifact rendering pipeline ─────────────


def diagram_rendering_pipeline(out_path: Path) -> None:
    """DesignSpec → artifact-type branch → per-target renderers + output files."""
    W, H = 2000, 1500
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)

    _title(d, W, "OpenDesign · 3-Artifact Rendering Pipeline",
           "One DesignSpec → three first-class output families · round-trip editable")

    # Top: DesignSpec (the shared input)
    spec = Box(W // 2 - 320, 200, 640, 110,
               "DesignSpec",
               "canvas · palette · typography · layer_graph\n"
               "+ artifact_type (poster | deck | landing)",
               fill=FILL_ACCENT_SOFT, title_pt=34, subtitle_pt=16)
    _draw_box(d, spec)

    # Three columns — one per artifact
    col_y = 400
    col_h = 360
    col_w = 560
    gap = 40
    total_w = 3 * col_w + 2 * gap
    start_x = (W - total_w) // 2

    columns = [
        {
            "title": "POSTER",
            "sub": "absolutely-positioned layers\nover a text-free background",
            "renderers": [
                ("PSD", "psd-tools · named pixel layers\n+ 'text' group"),
                ("SVG", "svgwrite · real <text> vector\n+ subsetted WOFF2 @font-face"),
                ("HTML", "contenteditable toolbar\n+ inlined fonts / images"),
                ("preview.png", "PIL alpha_composite\naspect-preserve fit"),
            ],
            "fill": FILL_SOFT,
        },
        {
            "title": "LANDING",
            "sub": "flow-layout HTML one-pager\n6 bundled design systems",
            "renderers": [
                ("index.html", "semantic sections\n+ CTA / nav / reveal (v1.3)"),
                ("<table>", "native HTML · winner bolding"),
                ("<figure>", "inline data: URI images"),
                ("preview.png", "stacked-section wireframe"),
            ],
            "fill": FILL_ACCENT_SOFT,
        },
        {
            "title": "DECK",
            "sub": "editable PPTX\nN slides · native TextFrames",
            "renderers": [
                ("deck.pptx", "python-pptx · live editable\nin PowerPoint / Keynote"),
                ("add_table", "native PPTX tables\n(v1.2 · bold-winner cells)"),
                ("slides/", "slide_NN.png\nper-slide thumbnails"),
                ("preview.png", "grid mosaic (2-/3-col)"),
            ],
            "fill": FILL_SOFT,
        },
    ]

    cols: list[dict] = []
    header_h = 120
    for i, col in enumerate(columns):
        x = start_x + i * (col_w + gap)
        header = Box(x, col_y, col_w, header_h, col["title"], col["sub"],
                     fill=col["fill"], title_pt=32, subtitle_pt=16,
                     title_color=ACCENT if col["title"] == "LANDING" else INK)
        _draw_box(d, header)

        # renderer cards stacked below — bigger h so two-line subtitles don't clip
        r_y = col_y + header_h + 16
        r_h = 72
        r_gap = 12
        for name, sub in col["renderers"]:
            r = Box(x, r_y, col_w, r_h, name, sub, fill=FILL_SOFT,
                    title_pt=20, subtitle_pt=13)
            _draw_box(d, r)
            r_y += r_h + r_gap

        # Spec → header arrow
        _arrow(d, spec.cx, spec.bottom,
               header.cx, header.y, color=ACCENT if col["title"] == "LANDING" else RULE)

        cols.append({"header": header})

    # Bottom strip: the feature-surface summary — placed BELOW the last
    # renderer card so text doesn't overlap. last-renderer bottom ≈
    # col_y + header_h + 16 + 4*(r_h+r_gap) - r_gap = 400+120+16+4*84-12 = 860
    summary_y = 920
    summary_items = [
        ("Aspect-preserve composite", "v1.2.3 · images letterbox,\ntables re-render at bbox"),
        ("Text-overlap detector", "v1.2.4 · glyph-inclusive\ncollision warn"),
        ("Figure ↔ text xref", "v1.2.4 · orphan-figure penalty"),
        ("Paper → editable tables", "v1.2 · ingest → kind=\"table\""),
    ]
    item_w = 440
    item_h = 120
    item_gap = 26
    total_iw = 4 * item_w + 3 * item_gap
    item_start = (W - total_iw) // 2
    for i, (t, s) in enumerate(summary_items):
        x = item_start + i * (item_w + item_gap)
        b = Box(x, summary_y, item_w, item_h, t, s, fill=FILL_SOFT,
                title_pt=18, subtitle_pt=14)
        _draw_box(d, b)

    _footer(d, W, H, "composite dispatches on DesignSpec.artifact_type · tools/composite.py")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    im.save(out_path, "PNG", optimize=True)


# ───────────────────── Diagram 3: paper2any flow ────────────────────────


def diagram_paper2any_flow(out_path: Path) -> None:
    """Input dispatch → extraction backend → VLM → layer registry → planner."""
    W, H = 2000, 1100
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)

    _title(d, W, "OpenDesign · paper2any · Ingestion Flow",
           "Any paper / deck / doc → structured manifest + ingest_fig_NN / ingest_table_NN layers")

    # Left column: input types
    inputs = [
        ".pdf  (native + scanned)",
        ".docx  (Word)",
        ".pptx  (PowerPoint)",
        ".md / .txt",
        ".png / .jpg / .webp",
    ]
    in_x = 80
    in_y = 220
    in_w = 340
    in_h = 64
    in_gap = 14
    for i, text in enumerate(inputs):
        b = Box(in_x, in_y + i * (in_h + in_gap), in_w, in_h, text, "",
                fill=FILL_SOFT, title_pt=20)
        _draw_box(d, b)

    # Middle column — three backends
    bk_x = 560
    bk_w = 480
    pdf_bk = Box(bk_x, 220, bk_w, 170,
                 "pymupdf",
                 "extract_image(xref) · native raster\n"
                 "get_drawings() · vector @300 dpi\n"
                 "find_tables() · table localization",
                 fill=FILL_ACCENT_SOFT, title_pt=26, subtitle_pt=14)
    structural_bk = Box(bk_x, 420, bk_w, 130,
                        "python-docx / python-pptx",
                        "heading-style paragraphs → sections\n"
                        "embedded images → figures (no VLM)",
                        fill=FILL_SOFT, title_pt=22, subtitle_pt=14)
    ocr_bk = Box(bk_x, 580, bk_w, 130,
                 "Qwen-VL-Max OCR (scanned PDF fallback)",
                 "v1.2.5 · 200 dpi · 6 workers parallel\n"
                 "triggers when detect_scanned_pdf=True",
                 fill=FILL_SOFT, title_pt=18, subtitle_pt=14)
    passthrough_bk = Box(bk_x, 740, bk_w, 90,
                         "passthrough",
                         ".md embeds → copy · image → register",
                         fill=FILL_SOFT, title_pt=22, subtitle_pt=14)

    for b in (pdf_bk, structural_bk, ocr_bk, passthrough_bk):
        _draw_box(d, b)

    # Input → backend arrows
    def _in_box(idx: int) -> tuple[int, int]:
        y = in_y + idx * (in_h + in_gap) + in_h // 2
        return in_x + in_w, y

    # .pdf → pymupdf (+ ocr fallback)
    x, y = _in_box(0)
    _arrow(d, x, y, pdf_bk.x, pdf_bk.cy - 30)
    _arrow(d, x, y + 20, ocr_bk.x, ocr_bk.cy, color=ACCENT)
    _label(d, (x + ocr_bk.x) // 2 - 60, y + 32, "if scanned",
           pt=13, color=ACCENT)
    # .docx / .pptx → structural
    for idx in (1, 2):
        x, y = _in_box(idx)
        _arrow(d, x, y, structural_bk.x, structural_bk.cy)
    # md / image → passthrough
    for idx in (3, 4):
        x, y = _in_box(idx)
        _arrow(d, x, y, passthrough_bk.x, passthrough_bk.cy)

    # Right column: VLM step + layer registry
    vlm_x = 1220
    vlm = Box(vlm_x, 220, 640, 170,
              "Qwen-VL-Max (OpenRouter)",
              "structure manifest · caption matching\n"
              "fake-figure filter · table cell parsing\n"
              "ThreadPool(6) — parallel calls",
              fill=FILL_ACCENT_SOFT, title_pt=24, subtitle_pt=15)
    _draw_box(d, vlm)
    _arrow(d, pdf_bk.right, pdf_bk.cy - 30, vlm.x, vlm.cy)

    registry = Box(vlm_x, 460, 640, 370,
                   "rendered_layers registry",
                   "",
                   fill=FILL_SOFT, title_pt=26)
    _draw_box(d, registry)

    # Inside registry: list the layer kinds
    inside_items = [
        ("ingest_fig_NN  (kind=\"image\")",
         "native-res PNG · caption · source_page"),
        ("ingest_table_NN  (kind=\"table\")",
         "rows · headers · col_highlight_rule · caption"),
        ("ingest_img_<sha8>  (kind=\"image\")",
         "md / image passthrough · sha-indexed"),
    ]
    inner_y = 520
    for title, sub in inside_items:
        b = Box(registry.x + 20, inner_y, registry.w - 40, 85,
                title, sub, fill=BG, border=RULE, border_w=1,
                title_pt=18, subtitle_pt=13)
        _draw_box(d, b)
        inner_y += 95

    # VLM + structural + ocr + passthrough → registry
    for src in (vlm, structural_bk, ocr_bk, passthrough_bk):
        _arrow(d, src.right, src.cy, registry.x, registry.cy)

    # Registry → planner
    planner_card = Box(vlm_x + 80, 880, 480, 80,
                       "Planner tool_result summary",
                       "top-20 figure catalog · ranked by (caption, vector, size)",
                       fill=FILL_ACCENT_SOFT, title_pt=20, subtitle_pt=13)
    _draw_box(d, planner_card)
    _arrow(d, registry.cx, registry.bottom, planner_card.cx, planner_card.y,
           color=ACCENT)

    _footer(d, W, H,
            "util/pdf.py  ·  tools/ingest_document.py  ·  util/vlm.py  ·  shipped v1.2 → v1.2.5")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    im.save(out_path, "PNG", optimize=True)


def main() -> None:
    outputs = [
        ("agent_architecture.png", diagram_agent_architecture),
        ("rendering_pipeline.png", diagram_rendering_pipeline),
        ("paper2any_flow.png", diagram_paper2any_flow),
    ]
    for name, fn in outputs:
        out = OUT_DIR / name
        fn(out)
        print(f"  wrote {out.relative_to(REPO_ROOT)}  ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
