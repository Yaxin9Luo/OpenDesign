"""ingest_document — v1.2 paper2any entry point.

v1.1 asked a VLM to locate figure bboxes on rasterized PDF pages. That
was unreliable: the model returned half-page screenshots, clipped
diagrams, and hallucinated "figures" on text-only pages.

v1.2 separates two concerns:

1. **Figure localization** is now done by **pymupdf directly** —
   `doc.extract_image(xref)` pulls embedded raster images at native
   resolution (e.g. 1890×1211 PNGs the paper author uploaded), and
   `page.get_drawings()` + proximity clustering + 300 dpi rendering
   catches vector-drawn architecture diagrams. No VLM guessing.

2. **Reading / matching** is still VLM work — but the default model
   is now Qwen-VL-Max via OpenRouter (~5× cheaper and faster than
   Claude Sonnet for this non-reasoning workload). Two calls:

     a. **Structure extraction**: render pages at 144 dpi, send as
        multi-image request, receive title / authors / abstract /
        sections / figures / tables JSON. `figures` now carries only
        `{caption, page, description}` — no bbox or idx (we already
        have bboxes from pymupdf).

     b. **Caption matching**: for each pymupdf candidate, ask the VLM
        which caption matches and whether it's a real figure. Fake
        candidates (logos, page headers, equation renders) are
        filtered at this step.

The `rendered_layers` record shape is unchanged (see the downstream
contract in `docs/DECISIONS.md` and `longcat_design/tools/composite.py`
hydration helpers): callers reference ingested figures by the stable
`layer_id` we register (e.g. `ingest_fig_01`), and the renderer
hydrates `src_path` / `bbox` / `caption` from the layer registry.

Markdown and image branches are untouched.
"""

from __future__ import annotations

import re
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import fitz  # pymupdf

from ._contract import ToolContext, obs_error, obs_ok
from ..schema import ToolObservation
from ..util.io import sha256_file
from ..util.logging import log
from ..util.pdf import (
    PdfFigureCandidate,
    ScannedPdfError,
    dedup_raster_vector,
    detect_scanned_pdf,
    extract_embedded_rasters,
    extract_page_text,
    extract_vector_clusters,
    page_count,
    render_page_png,
)
from ..util.vlm import VlmImage, vlm_call_json


# Max PDF bytes we accept in one call (belt-and-suspenders — pymupdf
# itself can open almost anything, but ingest touches every page and
# we want to fail fast on pathological inputs rather than spin).
_MAX_PDF_BYTES = 24 * 1024 * 1024  # 24 MB
_MAX_PDF_PAGES = 80

_MD_IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")

# Max parallel caption-matching calls. VLM calls are HTTP-bound; 4–6 in
# flight keeps wall time tight without tripping OpenRouter rate limits.
_CAPTION_MATCH_PARALLELISM = 6
# Confidence floor for accepting a VLM caption match — below this we
# still keep the figure but flag it; `is_real_figure=false` drops it
# regardless of confidence.
_CAPTION_MATCH_MIN_CONFIDENCE = 0.35
# DPI for the cover-page image handed to the VLM during structure
# extraction. We only send ONE image (the front page) — the rest of
# the paper reaches the VLM as pymupdf-extracted text, which is both
# cheaper and dodges Qwen-VL-Max's per-request image-count cap
# (DashScope rejects >10 images with HTTP 400 "invalid_parameter").
_STRUCTURE_PAGE_DPI = 144
# Total text budget across all pages sent to the structure extractor.
# ~60k chars fits comfortably in the ~16k-token context window Qwen
# uses for this call while leaving headroom for the JSON response.
_STRUCTURE_TOTAL_TEXT_CAP = 60_000


_INGEST_STRUCTURE_PROMPT = """\
You are a document-structure extractor for LongcatDesign. You will be
given ONE cover-page image (page 1) for visual grounding, followed by
the full extracted text of the paper (all pages, marked with
[PAGE N] headers). Return a STRICT JSON manifest that downstream tools
consume verbatim.

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
    {"caption": "<figure's full caption text, as it appears in the doc>",
     "page": <1-indexed page where the figure is anchored>,
     "description": "<1 sentence describing what's in the figure; used later to match it to a crop>"},
    ...
  ],
  "tables": [
    {"caption": "<table caption>", "page": <int>},
    ...
  ],
  "key_quotes": ["<memorable line from the doc>", ...]
}
```

Rules:
- Titles: use the human-facing title, not the first line.
- Sections: include each top-level heading (or the logical equivalent
  if the doc doesn't have explicit headings). Max ~10 sections; if the
  doc has more, collapse aggressively (group related subsections).
- Figures: only include REAL visual figures (diagrams, charts,
  screenshots, photos). Ignore logos, page headers, decorative borders,
  page numbers, watermarks, and inline equation renders. Captions as
  they literally appear in the doc. Include sub-panels only if they
  have their own caption line.
- Pages: 1-indexed.
- Empty lists are fine. Don't guess.
- No extra prose outside the fenced JSON block.
"""


_CAPTION_MATCH_PROMPT = """\
You are a figure↔caption matcher. You will see ONE image cropped from
a PDF plus a short list of figure-caption candidates pulled from the
same paper. Pick the caption that belongs to the image, or report that
the image is not a real figure.

Output **a single fenced JSON code block, nothing else**:

```json
{
  "matched_idx": <int index into the candidate list, or null>,
  "confidence": <float 0.0–1.0>,
  "is_real_figure": <true | false>,
  "reason": "<short explanation>"
}
```

Rules:
- `matched_idx` indexes the candidate list the user gives you (0-based).
- If none of the captions match OR the image is a logo, page header,
  publisher mark, decorative border, watermark, or equation render
  (not a real figure), set `matched_idx=null` and
  `is_real_figure=false`.
- If the image is a real figure but its caption isn't in the
  candidate list (the list was truncated), set `matched_idx=null` and
  `is_real_figure=true`. Downstream will keep it with an empty caption.
- Prefer confidence ≥ 0.7 when you're sure; otherwise be honest and
  use a lower value.
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
        except ScannedPdfError as e:
            return obs_error(f"ingest failed on {fp.name}: {e}")
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
            f"{_MAX_PDF_BYTES / 1_048_576:.0f} MB). Split the document."
        )
    pages = page_count(fp)
    if pages > _MAX_PDF_PAGES:
        raise RuntimeError(
            f"PDF has {pages} pages (cap {_MAX_PDF_PAGES}). Trim the document."
        )

    doc = fitz.open(fp)
    try:
        if detect_scanned_pdf(doc):
            raise ScannedPdfError(
                f"{fp.name} appears to be a scanned PDF (no extractable "
                "text or vector drawings). OCR is not supported in v1.2."
            )

        # 1. pymupdf figure candidates (no LLM).
        candidates: list[PdfFigureCandidate] = []
        candidates.extend(extract_embedded_rasters(doc, ctx.layers_dir))
        candidates.extend(extract_vector_clusters(doc, ctx.layers_dir))
        candidates = dedup_raster_vector(candidates)
        log("ingest.pdf.candidates", file=fp.name,
            raster=sum(1 for c in candidates if c.strategy == "raster"),
            vector=sum(1 for c in candidates if c.strategy == "vector"))

        # 2. Page text (lossless, free). Feeds the VLM as text instead
        # of shipping 43 page images — Qwen-VL-Max on DashScope rejects
        # multi-image payloads over ~10 images, and text is denser
        # information per token than rasterized pages anyway.
        page_texts = extract_page_text(doc)
    finally:
        doc.close()

    # Cover-page image: one rasterized page just for visual title/logo
    # grounding. The rest of the paper reaches the VLM via text.
    cover_png = ctx.layers_dir / "ingest_page_001.png"
    try:
        render_page_png(fp, 1, cover_png, dpi=_STRUCTURE_PAGE_DPI)
    except Exception as e:
        log("ingest.pdf.render_fail", page=1, error=str(e))
        cover_png = None  # type: ignore[assignment]

    manifest = _extract_structure(page_texts, cover_png, ctx, fp)
    _normalize_manifest_lists(manifest)

    # 3. Caption matching — per candidate, parallelized.
    registered_layer_ids: list[str] = []
    if candidates:
        matches = _match_captions_parallel(candidates, manifest, ctx)
        registered_layer_ids = _register_candidates(
            candidates=candidates, matches=matches, ctx=ctx, pdf_path=fp,
        )
    else:
        log("ingest.pdf.no_candidates", file=fp.name,
            note="pymupdf returned 0 figures; VLM manifest may still be useful")

    return {
        "file": str(fp), "type": "pdf", "manifest": manifest,
        "registered_layer_ids": registered_layer_ids,
        "summary": f"{manifest.get('title', '?')} — "
                   f"{len(registered_layer_ids)} figure(s), "
                   f"{len(manifest.get('sections', []))} section(s)",
    }


def _extract_structure(
    page_texts: list[str],
    cover_png: Path | None,
    ctx: ToolContext,
    fp: Path,
) -> dict[str, Any]:
    import time as _time

    # Concatenate text with [PAGE N] headers; budget the total so the
    # VLM's context doesn't blow up on reference-heavy long papers.
    body_lines: list[str] = []
    used = 0
    for page_num, text in enumerate(page_texts, start=1):
        chunk = f"\n[PAGE {page_num}]\n{text.strip()}"
        if used + len(chunk) > _STRUCTURE_TOTAL_TEXT_CAP:
            body_lines.append(
                f"\n[remaining {len(page_texts) - page_num + 1} "
                f"pages omitted — cap {_STRUCTURE_TOTAL_TEXT_CAP} chars]"
            )
            break
        body_lines.append(chunk)
        used += len(chunk)
    full_text = "".join(body_lines)

    user_text = (
        "Below is the complete extracted text of the paper, followed by "
        "the cover-page image for visual grounding. Extract the "
        "structured manifest as JSON per the system prompt. Return "
        "STRICT JSON only.\n\n"
        f"{full_text}"
    )

    images = [VlmImage.from_path(cover_png)] if cover_png is not None else []

    log("ingest.pdf.structure.request",
        file=fp.name, text_chars=used, n_pages=len(page_texts),
        cover_image=cover_png is not None,
        model=ctx.settings.ingest_model,
        timeout_s=ctx.settings.ingest_http_timeout)
    t0 = _time.monotonic()
    manifest = vlm_call_json(
        settings=ctx.settings,
        model=ctx.settings.ingest_model,
        system=_INGEST_STRUCTURE_PROMPT,
        user_text=user_text,
        images=images,
        max_tokens=8192,
    )
    log("ingest.pdf.structure.response",
        file=fp.name, wall_s=round(_time.monotonic() - t0, 1))
    return manifest


def _normalize_manifest_lists(manifest: dict[str, Any]) -> None:
    """Coerce None → [] on list-shaped keys. Claude/Qwen occasionally
    emit `"figures": null` for papers they find no figures in, which
    breaks downstream len() calls."""
    for key in ("sections", "figures", "tables", "authors", "key_quotes"):
        if not isinstance(manifest.get(key), list):
            manifest[key] = []


def _match_captions_parallel(
    candidates: list[PdfFigureCandidate],
    manifest: dict[str, Any],
    ctx: ToolContext,
) -> dict[int, dict[str, Any]]:
    """Run caption matching for each candidate in a thread pool.

    Returns a dict keyed by candidate index → match result dict
    (`matched_idx`, `confidence`, `is_real_figure`, `reason`, and
    `caption_text` filled in from the manifest for convenience).
    """
    all_figs = list(manifest.get("figures", []))
    if not all_figs:
        # No captions to match — keep the raw candidates, no caption.
        return {
            i: {"matched_idx": None, "confidence": 0.0,
                "is_real_figure": True, "reason": "no captions in manifest",
                "caption_text": ""}
            for i in range(len(candidates))
        }

    results: dict[int, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=_CAPTION_MATCH_PARALLELISM) as ex:
        futures = {
            ex.submit(_match_one_caption, i, cand, all_figs, ctx): i
            for i, cand in enumerate(candidates)
        }
        for fut in as_completed(futures):
            i = futures[fut]
            try:
                results[i] = fut.result()
            except Exception as e:
                log("ingest.pdf.caption_match_fail",
                    cand_idx=i, error=str(e))
                results[i] = {
                    "matched_idx": None, "confidence": 0.0,
                    "is_real_figure": True,
                    "reason": f"match call failed: {e}",
                    "caption_text": "",
                }
    return results


def _match_one_caption(
    cand_idx: int,
    candidate: PdfFigureCandidate,
    all_figs: list[dict[str, Any]],
    ctx: ToolContext,
) -> dict[str, Any]:
    # Filter captions to the candidate's page ± 1, fall back to whole
    # manifest if the page window is empty (handles figures whose caption
    # ends up on the next page due to column overflow).
    near: list[tuple[int, dict[str, Any]]] = [
        (i, f) for i, f in enumerate(all_figs)
        if abs(int(f.get("page", 0)) - candidate.page) <= 1
    ]
    pool = near if near else list(enumerate(all_figs))
    # Indices in `pool` are *relative* to the pool list we show the VLM;
    # we need to remap back to the full `all_figs` index.
    local_to_global = {local_i: global_i for local_i, (global_i, _) in enumerate(pool)}

    lines = []
    for local_i, (_, fig) in enumerate(pool):
        cap = (fig.get("caption") or "").replace("\n", " ")[:240]
        lines.append(f"  [{local_i}] (p.{fig.get('page', '?')}) {cap}")
    user_text = (
        f"Candidate captions near page {candidate.page} "
        f"(strategy: {candidate.strategy}):\n"
        + "\n".join(lines)
        + "\n\nReturn the JSON described in the system prompt."
    )

    result = vlm_call_json(
        settings=ctx.settings,
        model=ctx.settings.ingest_model,
        system=_CAPTION_MATCH_PROMPT,
        user_text=user_text,
        images=[VlmImage.from_path(candidate.path)],
        max_tokens=512,
    )

    # Remap local index → global manifest index, attach caption text.
    local_idx = result.get("matched_idx")
    global_idx = None
    caption_text = ""
    if isinstance(local_idx, int) and local_idx in local_to_global:
        global_idx = local_to_global[local_idx]
        caption_text = str(all_figs[global_idx].get("caption", ""))

    return {
        "matched_idx": global_idx,
        "confidence": float(result.get("confidence", 0.0) or 0.0),
        "is_real_figure": bool(result.get("is_real_figure", True)),
        "reason": str(result.get("reason", ""))[:200],
        "caption_text": caption_text,
    }


def _register_candidates(
    *,
    candidates: list[PdfFigureCandidate],
    matches: dict[int, dict[str, Any]],
    ctx: ToolContext,
    pdf_path: Path,
) -> list[str]:
    """Apply caption matches + fake-figure filter. Register survivors
    in `ctx.state["rendered_layers"]` with the downstream contract
    schema, then rename their PNGs to `img_{layer_id}.png` so the
    layers directory stays tidy."""
    registered: list[str] = []
    next_idx = 1

    for cand_idx, cand in enumerate(candidates):
        match = matches.get(cand_idx, {})
        is_real = bool(match.get("is_real_figure", True))
        if not is_real:
            log("ingest.pdf.reject_fake",
                page=cand.page, strategy=cand.strategy,
                reason=match.get("reason", ""), path=cand.path.name)
            # Clean up the rejected PNG so we don't bloat the run dir.
            try:
                cand.path.unlink()
            except OSError:
                pass
            continue

        layer_id = f"ingest_fig_{next_idx:02d}"
        next_idx += 1
        final_path = ctx.layers_dir / f"img_{layer_id}.png"
        try:
            if cand.path.resolve() != final_path.resolve():
                shutil.move(str(cand.path), str(final_path))
        except OSError as e:
            log("ingest.pdf.rename_fail",
                layer_id=layer_id, error=str(e))
            final_path = cand.path

        ctx.state["rendered_layers"][layer_id] = {
            "layer_id": layer_id,
            "name": f"figure_{next_idx - 1}",
            "kind": "image",
            "z_index": 5,
            "bbox": None,
            "src_path": str(final_path),
            "aspect_ratio": _aspect_from_dims(cand.width_px, cand.height_px),
            "image_size": f"{cand.width_px}x{cand.height_px}",
            "sha256": sha256_file(final_path),
            "source": "ingested_pdf",
            "source_file": str(pdf_path),
            "source_page": cand.page,
            "caption": match.get("caption_text", ""),
            "extract_strategy": cand.strategy,   # for debugging
            "caption_confidence": match.get("confidence", 0.0),
        }
        registered.append(layer_id)

    log("ingest.pdf.register",
        kept=len(registered),
        dropped=len(candidates) - len(registered))
    return registered


# ───────────────────────── Markdown branch ─────────────────────────────

def _ingest_markdown(fp: Path, ctx: ToolContext) -> dict[str, Any]:
    text = fp.read_text(encoding="utf-8")
    registered: list[str] = []
    skipped: list[str] = []

    for m in _MD_IMG_RE.finditer(text):
        alt_text = m.group(1)
        ref = m.group(2).strip()
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
