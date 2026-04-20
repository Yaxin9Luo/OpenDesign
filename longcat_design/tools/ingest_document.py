"""ingest_document — v1.1 paper2any entry point.

Polymorphic by file extension:

  .pdf  →  Anthropic native `document` content block for structure
           extraction + pymupdf for figure cropping. Every extracted figure
           lands in `ctx.state["rendered_layers"]` with a stable
           `layer_id` so the planner can just reference it in
           `propose_design_spec` (no separate `passthrough_image` call).
  .md / .markdown / .txt
        →  Read as text, resolve any `![](image.png)` refs relative to the
           file, copy resolved images into `ctx.layers_dir` + register.
  .png / .jpg / .jpeg / .webp
        →  Copy file into `ctx.layers_dir`, register as a single image
           layer. Used for ad-hoc user-attached logos / photos.

Returns a summary the planner can use + writes a full manifest into
`ctx.state["ingested"]` (list of per-file dicts) for downstream
`propose_design_spec` consumption.
"""

from __future__ import annotations

import base64
import json
import re
import shutil
from pathlib import Path
from typing import Any

from anthropic import Anthropic

from ._contract import ToolContext, obs_error, obs_ok
from ..schema import ToolObservation
from ..util.io import sha256_file
from ..util.logging import log
from ..util.pdf import crop_bbox, page_count, render_page_png


_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)

# Max PDF bytes we send in one Anthropic call. Anthropic caps documents at
# ~32 MB base64-encoded; we stay well under. Larger PDFs are rejected with
# a redirect (user can split them — v1.1.5 will add chunking).
_MAX_PDF_BYTES = 24 * 1024 * 1024  # 24 MB
# Hard cap on page count per request (Anthropic: 100 pages; we leave headroom).
_MAX_PDF_PAGES = 80

_MD_IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


_INGEST_STRUCTURE_PROMPT = """\
You are a document-structure extractor for LongcatDesign. You will be given
a PDF via a `document` content block. Return a STRICT JSON manifest that
downstream tools can consume verbatim. The planner uses this to generate a
poster / landing page / slide deck from the document.

Output **a single fenced JSON code block, nothing else**:

```json
{
  "title": "<paper/doc title>",
  "authors": ["<Author Name>", ...],
  "venue": "<conference / publication / null>",
  "abstract": "<2-4 sentence abstract>",
  "sections": [
    {"idx": 1, "heading": "Introduction",
     "summary": "<2-3 sentences of what this section argues>",
     "key_points": ["<one-liner>", "<one-liner>", ...]},
    ...
  ],
  "figures": [
    {"idx": 1, "caption": "<figure's full caption text>",
     "page": <1-indexed page number>,
     "description": "<what's in the figure — 1 sentence, used to match bbox>"},
    ...
  ],
  "tables": [
    {"idx": 1, "caption": "<table caption>", "page": <int>},
    ...
  ],
  "key_quotes": ["<memorable line from the doc>", ...]
}
```

Rules:
- Titles: use the human-facing title, not the first line.
- Sections: include each top-level heading (or the logical equivalent if
  the doc doesn't have explicit headings). Max ~10 sections; if the doc
  has more, collapse aggressively (group related subsections).
- Figures: only include actual figures (diagrams, charts, screenshots,
  photos). Ignore logos, decorative borders, page numbers. Captions as
  they literally appear in the doc.
- Pages: 1-indexed.
- Empty lists are fine. Don't guess.
- No extra prose outside the fenced JSON block.
"""


_BBOX_LOCATOR_PROMPT = """\
You will see an image of ONE rendered page from a PDF. You'll also see a
list of figures that appear on this page with their captions. For EACH
figure, return a pixel-bbox (x, y, w, h) that tightly encloses the figure
artwork — NOT including the caption text beneath/above.

Pixel coordinates use top-left origin; (x, y) is the top-left corner of
the bbox; w + h are in pixels. The page image dimensions are given to you
in the user prompt.

Output **a single fenced JSON code block, nothing else**:

```json
{
  "figures": [
    {"idx": <figure index as given>, "bbox": [x, y, w, h]},
    ...
  ]
}
```

Rules:
- Bbox must be within image dimensions.
- If a figure spans most of the page, that's fine; a wide bbox is allowed.
- If you genuinely cannot find a figure (maybe it was mis-identified in
  the initial structure extraction), omit it from the response — don't
  invent coordinates.
"""


def ingest_document(args: dict[str, Any], *, ctx: ToolContext) -> ToolObservation:
    raw = args.get("file_paths")
    if not raw or not isinstance(raw, list):
        return obs_error(
            "ingest_document needs 'file_paths': list[str]",
            next_actions=["pass an array of absolute or ~-prefixed paths"],
        )

    summaries: list[dict[str, Any]] = []
    artifact_paths: list[str] = []

    for fp_str in raw:
        fp = Path(str(fp_str)).expanduser()
        if not fp.is_absolute():
            fp = fp.resolve()
        if not fp.exists():
            return obs_error(f"file not found: {fp}")
        if not fp.is_file():
            return obs_error(f"not a regular file: {fp}")

        ext = fp.suffix.lower()
        log("ingest.start", file=str(fp), ext=ext, bytes=fp.stat().st_size)

        try:
            if ext == ".pdf":
                s = _ingest_pdf(fp, ctx)
            elif ext in (".md", ".markdown", ".txt"):
                s = _ingest_markdown(fp, ctx)
            elif ext in (".png", ".jpg", ".jpeg", ".webp"):
                s = _ingest_image(fp, ctx)
            else:
                return obs_error(
                    f"unsupported file type {ext!r}; supported: "
                    ".pdf, .md/.markdown/.txt, .png/.jpg/.jpeg/.webp",
                )
        except RuntimeError as e:
            return obs_error(f"ingest failed on {fp.name}: {e}")

        summaries.append(s)
        for lid in s.get("registered_layer_ids", []):
            rec = ctx.state["rendered_layers"].get(lid)
            if rec and rec.get("src_path"):
                artifact_paths.append(rec["src_path"])

    ctx.state.setdefault("ingested", []).extend(summaries)
    log("ingest.done", files=len(summaries),
        total_figures=sum(len(s.get("registered_layer_ids", [])) for s in summaries))

    # Build a compact, planner-consumable summary.
    lines = []
    for s in summaries:
        f = Path(s["file"]).name
        t = s["type"]
        if t == "pdf":
            m = s["manifest"]
            lines.append(
                f"  • {f} (PDF): \"{m.get('title','?')}\" by "
                f"{', '.join(m.get('authors', [])[:3]) or 'unknown'} — "
                f"{len(m.get('sections', []))} section(s), "
                f"{len(s['registered_layer_ids'])} figure layer(s): "
                f"{s['registered_layer_ids']}"
            )
        elif t == "markdown":
            lines.append(
                f"  • {f} (MD): {s['n_chars']} chars, "
                f"{len(s['registered_layer_ids'])} embedded image layer(s): "
                f"{s['registered_layer_ids']}"
            )
        elif t == "image":
            lines.append(
                f"  • {f} (image): layer_id={s['registered_layer_ids'][0]}, "
                f"{s['width']}×{s['height']}"
            )

    return obs_ok(
        "Ingested " + str(len(summaries)) + " file(s):\n" + "\n".join(lines) +
        "\n\nAll image layers are pre-registered in rendered_layers — "
        "reference them by layer_id in propose_design_spec's layer_graph "
        "with kind: \"image\" and the renderer will hydrate src_path "
        "automatically.",
        artifacts=artifact_paths,
        next_actions=[
            "call propose_design_spec — pull title + sections from the "
            "ingested manifest; reference ingested figure layer_ids as "
            "image children in the appropriate artifact-type schema",
        ],
    )


# ───────────────────────────── PDF branch ──────────────────────────────

def _ingest_pdf(fp: Path, ctx: ToolContext) -> dict[str, Any]:
    size = fp.stat().st_size
    if size > _MAX_PDF_BYTES:
        raise RuntimeError(
            f"PDF too large ({size / 1_048_576:.1f} MB > "
            f"{_MAX_PDF_BYTES / 1_048_576:.0f} MB). Split into smaller "
            "files or wait for v1.1.5 chunking."
        )
    pages = page_count(fp)
    if pages > _MAX_PDF_PAGES:
        raise RuntimeError(
            f"PDF has {pages} pages (cap {_MAX_PDF_PAGES}). Trim or "
            "wait for v1.1.5 chunking."
        )

    client = _anthropic_client(ctx)
    import time as _time

    # Step 1: Claude-native structure extraction.
    # Sonnet default (via settings.ingest_model) is fast + cheap enough for
    # "extract title/sections/figures"; Opus would spend ~3-5× longer reading
    # the PDF with no quality win on this task. Override via INGEST_MODEL.
    b64 = base64.standard_b64encode(fp.read_bytes()).decode("ascii")
    log("ingest.pdf.structure.request", file=fp.name, pages=pages,
        bytes=size, model=ctx.settings.ingest_model,
        timeout_s=ctx.settings.ingest_http_timeout)
    _t0 = _time.monotonic()
    resp = client.messages.create(
        model=ctx.settings.ingest_model,
        max_tokens=8192,
        system=_INGEST_STRUCTURE_PROMPT,
        messages=[{
            "role": "user",
            "content": [
                {"type": "document",
                 "source": {"type": "base64",
                            "media_type": "application/pdf",
                            "data": b64}},
                {"type": "text",
                 "text": ("Extract the structured manifest for this "
                          "document. Return STRICT JSON only.")},
            ],
        }],
    )
    log("ingest.pdf.structure.response", file=fp.name,
        wall_s=round(_time.monotonic() - _t0, 1),
        in_tok=getattr(resp.usage, "input_tokens", None),
        out_tok=getattr(resp.usage, "output_tokens", None))
    manifest = _parse_json_block(resp.content)
    if not isinstance(manifest, dict):
        raise RuntimeError("structure extraction returned non-dict JSON")

    # Normalise sections/figures/tables to lists so downstream code is safe.
    # `setdefault` is a no-op when the key is PRESENT WITH None value, so we
    # explicitly coerce None → [] — Claude sometimes emits "figures": null for
    # papers it finds no visual figures in.
    for list_key in ("sections", "figures", "tables", "authors", "key_quotes"):
        if not isinstance(manifest.get(list_key), list):
            manifest[list_key] = []

    figures: list[dict[str, Any]] = list(manifest["figures"])
    registered_layer_ids: list[str] = []

    if not figures:
        log("ingest.pdf.structure.done", file=fp.name, figures=0,
            sections=len(manifest.get("sections", [])))
        return {
            "file": str(fp), "type": "pdf", "manifest": manifest,
            "registered_layer_ids": [],
            "summary": f"{manifest.get('title', '?')} — "
                       f"0 figures, {len(manifest.get('sections', []))} sections",
        }

    # Step 2: render each page that has figures (page-level caching saves
    # rendering the same page multiple times for multi-figure pages).
    pages_needed: dict[int, Path] = {}
    for fig in figures:
        try:
            page = int(fig.get("page", 0))
        except (TypeError, ValueError):
            continue
        if page < 1 or page > pages:
            continue
        fig["page"] = page
        if page not in pages_needed:
            page_png = ctx.layers_dir / f"ingest_page_{page:03d}.png"
            try:
                render_page_png(fp, page, page_png, dpi=192)
            except Exception as e:
                log("ingest.pdf.render_fail", page=page, error=str(e))
                continue
            pages_needed[page] = page_png

    log("ingest.pdf.structure.done", file=fp.name,
        figures=len(figures), sections=len(manifest.get("sections", [])),
        pages_rendered=len(pages_needed))

    # Step 3: for each rendered page, ask Claude for per-figure bboxes.
    for page_num, page_png in pages_needed.items():
        page_figs = [f for f in figures if int(f.get("page", 0)) == page_num]
        try:
            bboxes_by_idx = _locate_figure_bboxes(page_png, page_figs, client, ctx)
        except Exception as e:
            log("ingest.pdf.locator_fail", page=page_num, error=str(e))
            bboxes_by_idx = {}

        for fig in page_figs:
            fig_idx = int(fig.get("idx", 0))
            bbox = bboxes_by_idx.get(fig_idx)
            if bbox is None:
                # Fallback: whole page as the "figure."
                from PIL import Image as _Image
                with _Image.open(page_png) as im:
                    bbox = (0, 0, im.width, im.height)
                log("ingest.pdf.locator_fallback",
                    page=page_num, fig_idx=fig_idx, reason="no_bbox_from_model")

            cropped_path = ctx.layers_dir / f"img_ingest_fig_{fig_idx:02d}.png"
            try:
                cw, ch = crop_bbox(page_png, bbox, cropped_path)
            except Exception as e:
                log("ingest.pdf.crop_fail", fig_idx=fig_idx, error=str(e))
                continue

            layer_id = f"ingest_fig_{fig_idx:02d}"
            aspect = _aspect_from_dims(cw, ch)
            ctx.state["rendered_layers"][layer_id] = {
                "layer_id": layer_id,
                "name": f"figure_{fig_idx}",
                "kind": "image",
                "z_index": 5,
                "bbox": None,
                "src_path": str(cropped_path),
                "aspect_ratio": aspect,
                "image_size": f"{cw}x{ch}",
                "sha256": sha256_file(cropped_path),
                "source": "ingested_pdf",
                "source_file": str(fp),
                "source_page": page_num,
                "caption": str(fig.get("caption", "")),
            }
            fig["layer_id"] = layer_id
            registered_layer_ids.append(layer_id)

    return {
        "file": str(fp), "type": "pdf", "manifest": manifest,
        "registered_layer_ids": registered_layer_ids,
        "summary": f"{manifest.get('title', '?')} — "
                   f"{len(registered_layer_ids)} figure(s), "
                   f"{len(manifest.get('sections', []))} section(s)",
    }


def _locate_figure_bboxes(
    page_png: Path,
    page_figs: list[dict[str, Any]],
    client: Anthropic,
    ctx: ToolContext,
) -> dict[int, tuple[int, int, int, int]]:
    """Ask Claude vision for per-figure bboxes on a rendered page."""
    from PIL import Image as _Image
    with _Image.open(page_png) as im:
        iw, ih = im.size

    with page_png.open("rb") as fh:
        b64 = base64.standard_b64encode(fh.read()).decode("ascii")

    fig_lines = "\n".join(
        f"- idx={f.get('idx')}  caption: {f.get('caption', '')[:200]!r}"
        for f in page_figs
    )
    user_text = (
        f"Page image dimensions: {iw}×{ih} pixels (top-left origin).\n\n"
        f"Figures on this page:\n{fig_lines}\n\n"
        "Return a bbox per figure in the JSON schema specified in your "
        "system prompt. Bbox values must be ints within [0, width] / "
        "[0, height]."
    )

    resp = client.messages.create(
        model=ctx.settings.ingest_model,
        max_tokens=1024,
        system=_BBOX_LOCATOR_PROMPT,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image",
                 "source": {"type": "base64",
                            "media_type": "image/png",
                            "data": b64}},
                {"type": "text", "text": user_text},
            ],
        }],
    )
    payload = _parse_json_block(resp.content)
    out: dict[int, tuple[int, int, int, int]] = {}
    if not isinstance(payload, dict):
        return out
    for item in payload.get("figures", []):
        try:
            idx = int(item.get("idx"))
            x, y, w, h = item["bbox"]
            x, y, w, h = int(x), int(y), int(w), int(h)
            if w <= 0 or h <= 0:
                continue
            out[idx] = (x, y, w, h)
        except (TypeError, ValueError, KeyError):
            continue
    return out


# ───────────────────────── Markdown branch ─────────────────────────────

def _ingest_markdown(fp: Path, ctx: ToolContext) -> dict[str, Any]:
    text = fp.read_text(encoding="utf-8")
    registered: list[str] = []
    skipped: list[str] = []

    for m in _MD_IMG_RE.finditer(text):
        alt_text = m.group(1)
        ref = m.group(2).strip()
        # Only resolve relative / absolute filesystem paths; skip URLs.
        if ref.startswith(("http://", "https://", "data:")):
            skipped.append(ref[:80])
            continue
        src = Path(ref)
        if not src.is_absolute():
            src = (fp.parent / src).resolve()
        if not src.exists() or not src.is_file():
            skipped.append(str(src))
            continue
        try:
            layer_id = _register_image_file(src, ctx, name_hint=alt_text or src.stem)
        except RuntimeError:
            skipped.append(str(src))
            continue
        registered.append(layer_id)

    log("ingest.md.done", file=fp.name, chars=len(text),
        registered=len(registered), skipped=len(skipped))

    return {
        "file": str(fp), "type": "markdown",
        "raw_text": text,
        "n_chars": len(text),
        "registered_layer_ids": registered,
        "skipped_images": skipped,
        "summary": f"{fp.name} — {len(text)} chars, "
                   f"{len(registered)} image(s)"
                   + (f", {len(skipped)} skipped" if skipped else ""),
    }


# ─────────────────────────── Image branch ──────────────────────────────

def _ingest_image(fp: Path, ctx: ToolContext) -> dict[str, Any]:
    layer_id = _register_image_file(fp, ctx, name_hint=fp.stem)
    rec = ctx.state["rendered_layers"][layer_id]
    w_s, h_s = rec["image_size"].split("x")
    log("ingest.image.done", file=fp.name, layer_id=layer_id,
        w=int(w_s), h=int(h_s))
    return {
        "file": str(fp), "type": "image",
        "registered_layer_ids": [layer_id],
        "width": int(w_s), "height": int(h_s),
        "summary": f"{fp.name} — {w_s}×{h_s}",
    }


# ───────────────────────────── helpers ─────────────────────────────────

def _anthropic_client(ctx: ToolContext) -> Anthropic:
    """Dedicated client for ingest calls with an explicit longer timeout
    (default 10 min) so large-PDF requests fail fast if the connection
    stalls, instead of hanging indefinitely."""
    kwargs: dict[str, Any] = {
        "api_key": ctx.settings.anthropic_api_key,
        "timeout": ctx.settings.ingest_http_timeout,
        "max_retries": 1,  # retry once on transient network issues, no more
    }
    if ctx.settings.anthropic_base_url:
        kwargs["base_url"] = ctx.settings.anthropic_base_url
    return Anthropic(**kwargs)


def _parse_json_block(content_blocks: list[Any]) -> Any:
    text = "".join(
        getattr(b, "text", "") for b in content_blocks
        if getattr(b, "type", None) == "text"
    )
    m = _JSON_BLOCK_RE.search(text)
    payload = m.group(1) if m else text.strip()
    try:
        return json.loads(payload)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"model returned non-JSON output: {e}; got {text[:300]!r}")


def _register_image_file(src: Path, ctx: ToolContext, *, name_hint: str) -> str:
    from PIL import Image as _Image

    sha = sha256_file(src)
    ext = src.suffix.lower() if src.suffix else ".png"
    if ext == ".jpeg":
        ext = ".jpg"
    layer_id = f"ingest_img_{sha[:8]}"
    dest = ctx.layers_dir / f"img_{layer_id}{ext}"
    if not dest.exists():
        shutil.copy2(src, dest)

    try:
        with _Image.open(dest) as im:
            w, h = im.size
    except Exception as e:
        raise RuntimeError(f"image not readable: {src} ({e})")

    ctx.state["rendered_layers"][layer_id] = {
        "layer_id": layer_id,
        "name": _sanitize_name(name_hint) or layer_id,
        "kind": "image",
        "z_index": 5,
        "bbox": None,
        "src_path": str(dest),
        "aspect_ratio": _aspect_from_dims(w, h),
        "image_size": f"{w}x{h}",
        "sha256": sha,
        "source": "ingested",
        "source_file": str(src),
    }
    return layer_id


def _aspect_from_dims(w: int, h: int) -> str:
    if h <= 0 or w <= 0:
        return "1:1"
    from math import gcd
    g = gcd(w, h)
    return f"{w // g}:{h // g}" if max(w // g, h // g) <= 32 else (
        "16:9" if w > h else "3:4" if h > w else "1:1"
    )


def _sanitize_name(s: str) -> str:
    s = (s or "").strip()
    return re.sub(r"[^\w\- ]", "", s)[:60]
