"""pymupdf (fitz) helpers used by `tools/ingest_document.py` (v1.2).

v1.1 treated PDFs as raster sources and asked a VLM to guess figure
bboxes on rasterized pages. That was unreliable — produced half-page
crops, clipped diagrams, and hallucinated "figures" on text-only
pages. v1.2 extracts figures directly from PDF structure:

- `extract_embedded_rasters`: pulls every embedded image at its native
  resolution via `doc.extract_image(xref)`. Lossless — returns the
  PNG/JPEG bytes the paper author uploaded.
- `extract_vector_clusters`: clusters vector `get_drawings()` by
  proximity, renders each cluster at high dpi. Catches architecture
  diagrams + pipeline figures that are stored as vector paths.

The VLM is still used downstream for caption↔figure matching and
fake-figure filtering — not for localization.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz  # pymupdf
from PIL import Image


# ─────────────────────────── public types ─────────────────────────────

@dataclass(frozen=True)
class PdfFigureCandidate:
    """A figure candidate extracted from a PDF, on disk as a PNG.

    Produced by `extract_embedded_rasters` or `extract_vector_clusters`.
    Downstream (`tools/ingest_document._ingest_pdf`) dedups across the
    two strategies and then asks a VLM to match captions.
    """
    page: int                                 # 1-indexed page
    bbox_pt: tuple[float, float, float, float] | None  # PDF-point coords
    path: Path                                # absolute PNG path on disk
    width_px: int
    height_px: int
    strategy: str                             # "raster" | "vector"
    xref: int | None                          # PDF xref (raster only)


@dataclass(frozen=True)
class PdfTableCandidate:
    """A table candidate extracted from a PDF.

    pymupdf `page.find_tables()` is used for **localization only** — its
    cell-level splits are unreliable on paper layouts (we've observed
    headers jammed into one cell, math-equation arrays detected as
    tables, figure diagrams misclassified as tables). Downstream asks a
    VLM to read the cropped region and return clean structured data
    (or reject the candidate as "not a data table").
    """
    page: int
    bbox_pt: tuple[float, float, float, float]
    image_path: Path                          # 300 dpi PNG of the bbox
    width_px: int
    height_px: int
    raw_cells: list[list[str]]                # pymupdf's best-effort split
    nrows: int
    ncols: int


class ScannedPdfError(RuntimeError):
    """Raised when a PDF has no embedded images AND no vector drawings
    AND almost no extractable text — i.e. a scanned PDF we cannot
    mine figures from without OCR."""


# ─────────────────────── legacy thin wrappers ─────────────────────────
# Kept because `tools/ingest_document` still uses these on the rare
# fallback path, and `scripts/spike_pdf_figures.py` imports them.

def page_count(pdf_path: Path) -> int:
    """Number of pages in the PDF."""
    doc = fitz.open(pdf_path)
    try:
        return len(doc)
    finally:
        doc.close()


def render_page_png(pdf_path: Path, page_num: int, out_path: Path,
                    dpi: int = 192) -> tuple[int, int]:
    """Render 1-indexed `page_num` to `out_path` as PNG. Returns (width,
    height) of the rendered image in pixels.
    """
    doc = fitz.open(pdf_path)
    try:
        if page_num < 1 or page_num > len(doc):
            raise ValueError(
                f"page_num={page_num} out of range 1..{len(doc)} for {pdf_path.name}"
            )
        pix = doc[page_num - 1].get_pixmap(dpi=dpi)
        pix.save(str(out_path))
        return pix.width, pix.height
    finally:
        doc.close()


def crop_bbox(page_png: Path, bbox: tuple[int, int, int, int],
              out_path: Path) -> tuple[int, int]:
    """Crop `page_png` to `bbox = (x, y, w, h)` and save as PNG."""
    with Image.open(page_png) as img:
        iw, ih = img.size
        x, y, w, h = bbox
        x = max(0, min(x, iw - 1))
        y = max(0, min(y, ih - 1))
        w = max(1, min(w, iw - x))
        h = max(1, min(h, ih - y))
        cropped = img.crop((x, y, x + w, y + h))
        cropped.save(out_path, format="PNG", optimize=True)
        return cropped.width, cropped.height


def probe_pdf(pdf_path: Path) -> dict[str, Any]:
    """Lightweight metadata probe (bytes + page count + first-page size)."""
    data = pdf_path.read_bytes()
    doc = fitz.open(pdf_path)
    try:
        first = doc[0] if len(doc) > 0 else None
        size_pt = (first.rect.width, first.rect.height) if first else (0, 0)
        return {
            "bytes": len(data),
            "pages": len(doc),
            "first_page_size_pt": size_pt,
        }
    finally:
        doc.close()


# ──────────────────────── figure extraction ───────────────────────────

def extract_embedded_rasters(
    doc: fitz.Document,
    out_dir: Path,
    *,
    min_w: int = 120,
    min_h: int = 80,
) -> list[PdfFigureCandidate]:
    """Pull every embedded raster image from the PDF at its native
    resolution. Returns one `PdfFigureCandidate` per kept image.

    Dedup: the same xref can appear on many pages (headers, logos,
    footers). We register each xref only once (first page that hosts it).
    Channel fixup: CMYK / palette (P) / grayscale (L) modes are
    converted to RGB so downstream renderers don't corrupt colors.
    Size filter: drops images smaller than `min_w × min_h` (typically
    decorative icons, bullet glyphs, tiny badges).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    records: list[PdfFigureCandidate] = []
    seen_xrefs: set[int] = set()

    for page_num, page in enumerate(doc, start=1):
        for img_info in page.get_images(full=True):
            xref = img_info[0]
            if xref in seen_xrefs:
                continue

            try:
                extracted = doc.extract_image(xref)
            except Exception:
                # corrupt xref — skip quietly, caller logs totals
                continue
            seen_xrefs.add(xref)

            w = int(extracted.get("width", 0))
            h = int(extracted.get("height", 0))
            if w < min_w or h < min_h:
                continue

            ext = extracted.get("ext", "png")
            data = extracted["image"]

            raw_path = out_dir / f"_tmp_p{page_num:03d}_xref{xref}.{ext}"
            raw_path.write_bytes(data)
            png_path = out_dir / f"p{page_num:03d}_xref{xref}.png"

            # Normalize to RGB/RGBA PNG so CMYK/L/P don't leak through.
            try:
                with Image.open(raw_path) as im:
                    if im.mode not in ("RGB", "RGBA"):
                        im = im.convert("RGBA" if im.mode in ("LA", "P") else "RGB")
                    im.save(png_path, format="PNG", optimize=True)
                    out_w, out_h = im.size
            except Exception:
                try:
                    raw_path.unlink()
                except OSError:
                    pass
                continue

            try:
                raw_path.unlink()
            except OSError:
                pass

            records.append(PdfFigureCandidate(
                page=page_num,
                bbox_pt=None,  # raster xref has no reliable page bbox
                path=png_path,
                width_px=out_w,
                height_px=out_h,
                strategy="raster",
                xref=xref,
            ))

    return records


def _merge_rects(rects: list[fitz.Rect], tol: float) -> list[fitz.Rect]:
    """Union-merge overlapping or near-touching rects. O(n²) — fine for
    <1000 drawings per page. Expand by `tol` before testing overlap so
    arrows, axis ticks, and label boxes bundle into one cluster."""
    merged = [fitz.Rect(r) for r in rects]
    changed = True
    while changed:
        changed = False
        new: list[fitz.Rect] = []
        consumed = [False] * len(merged)
        for i, a in enumerate(merged):
            if consumed[i]:
                continue
            cur = fitz.Rect(a)
            probe = fitz.Rect(cur.x0 - tol, cur.y0 - tol,
                              cur.x1 + tol, cur.y1 + tol)
            for j in range(i + 1, len(merged)):
                if consumed[j]:
                    continue
                if probe.intersects(merged[j]):
                    cur |= merged[j]
                    consumed[j] = True
                    changed = True
                    probe = fitz.Rect(cur.x0 - tol, cur.y0 - tol,
                                      cur.x1 + tol, cur.y1 + tol)
            new.append(cur)
        merged = new
    return merged


def extract_vector_clusters(
    doc: fitz.Document,
    out_dir: Path,
    *,
    dpi: int = 300,
    min_side_pt: float = 80.0,
    merge_tol_pt: float = 12.0,
    max_area_frac: float = 0.80,
) -> list[PdfFigureCandidate]:
    """Cluster vector drawings by proximity and render each cluster at
    `dpi`. Filters: drop clusters whose shorter side is < `min_side_pt`
    (horizontal rules, underlines, header bars) or whose area exceeds
    `max_area_frac` of the page (full-page decorative overlays).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    records: list[PdfFigureCandidate] = []

    for page_num, page in enumerate(doc, start=1):
        drawings = page.get_drawings()
        if not drawings:
            continue

        page_area = page.rect.width * page.rect.height
        raw_bboxes = [fitz.Rect(d["rect"]) for d in drawings
                      if d.get("rect") is not None]
        if not raw_bboxes:
            continue

        clusters = _merge_rects(raw_bboxes, merge_tol_pt)

        keep: list[fitz.Rect] = []
        for c in clusters:
            if c.width < min_side_pt or c.height < min_side_pt:
                continue
            if (c.width * c.height) / page_area > max_area_frac:
                continue
            keep.append(c)

        for cidx, c in enumerate(keep, start=1):
            pix = page.get_pixmap(clip=c, dpi=dpi)
            png_path = out_dir / f"p{page_num:03d}_vec{cidx:02d}.png"
            pix.save(str(png_path))
            records.append(PdfFigureCandidate(
                page=page_num,
                bbox_pt=(round(c.x0, 2), round(c.y0, 2),
                         round(c.x1, 2), round(c.y1, 2)),
                path=png_path,
                width_px=pix.width,
                height_px=pix.height,
                strategy="vector",
                xref=None,
            ))

    return records


def extract_table_candidates(
    doc: fitz.Document,
    out_dir: Path,
    *,
    dpi: int = 300,
    min_rows: int = 2,
    min_cols: int = 2,
    min_side_pt: float = 60.0,
) -> list[PdfTableCandidate]:
    """Per-page table candidates. pymupdf `page.find_tables()` returns
    TableFinder objects; we take each one as a LOCALIZATION hint only —
    cell splits are often wrong, so the VLM parses the crop separately.

    Filters:
    - drop candidates below `min_rows × min_cols` (often false positives
      on single-row headers or key-value annotation lists),
    - drop candidates with bbox side < `min_side_pt` (tiny layout artifacts),
    - do NOT drop "full-page" candidates here — some papers genuinely
      have page-spanning tables and the VLM decides from content.

    Each candidate is rendered to a PNG at `dpi` so the VLM gets a
    high-resolution view of the actual cells + borders.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    records: list[PdfTableCandidate] = []

    for page_num, page in enumerate(doc, start=1):
        try:
            finder = page.find_tables()
        except Exception as e:
            log_ignore_msg = f"find_tables failed on page {page_num}: {e}"
            # Swallow per-page — some pages (especially heavily-annotated ones)
            # trip internal assertions; they aren't fatal for the document.
            import sys as _sys
            print(log_ignore_msg, file=_sys.stderr)
            continue

        for tidx, tbl in enumerate(finder.tables, start=1):
            rect = fitz.Rect(tbl.bbox)
            if rect.width < min_side_pt or rect.height < min_side_pt:
                continue
            try:
                raw_cells = tbl.extract() or []
            except Exception:
                raw_cells = []
            nrows = len(raw_cells)
            ncols = max((len(r) for r in raw_cells), default=0)
            if nrows < min_rows or ncols < min_cols:
                continue

            png_path = out_dir / f"p{page_num:03d}_tbl{tidx:02d}.png"
            pix = page.get_pixmap(clip=rect, dpi=dpi)
            pix.save(str(png_path))

            # Coerce cells to strings (pymupdf sometimes emits None).
            norm_cells = [[(c if c is not None else "") for c in row]
                          for row in raw_cells]

            records.append(PdfTableCandidate(
                page=page_num,
                bbox_pt=(round(rect.x0, 2), round(rect.y0, 2),
                         round(rect.x1, 2), round(rect.y1, 2)),
                image_path=png_path,
                width_px=pix.width,
                height_px=pix.height,
                raw_cells=norm_cells,
                nrows=nrows,
                ncols=ncols,
            ))

    return records


def dedup_tables_against_figures(
    tables: list[PdfTableCandidate],
    figures: list[PdfFigureCandidate],
    *,
    containment_frac: float = 0.70,
) -> list[PdfTableCandidate]:
    """Drop a table candidate when a same-page vector figure bbox covers
    ≥ `containment_frac` of its bbox — stops "figure that looks like a
    table" regions from being processed twice (once via caption matching
    on the figure side, once via VLM table parse).

    Raster figure candidates (no PDF-point bbox) are ignored for dedup.
    The check is asymmetric: we drop the TABLE, never the figure, because
    the figure path handles composite visuals better than the table path.
    """
    if not tables or not figures:
        return list(tables)

    # Index figures with bboxes by page.
    by_page: dict[int, list[PdfFigureCandidate]] = {}
    for f in figures:
        if f.bbox_pt is None or f.strategy != "vector":
            continue
        by_page.setdefault(f.page, []).append(f)

    keep: list[PdfTableCandidate] = []
    for t in tables:
        same_page_figs = by_page.get(t.page, [])
        tx0, ty0, tx1, ty1 = t.bbox_pt
        t_area = max(1.0, (tx1 - tx0) * (ty1 - ty0))
        dropped = False
        for f in same_page_figs:
            assert f.bbox_pt is not None  # guard above
            fx0, fy0, fx1, fy1 = f.bbox_pt
            ix0 = max(tx0, fx0); iy0 = max(ty0, fy0)
            ix1 = min(tx1, fx1); iy1 = min(ty1, fy1)
            if ix1 <= ix0 or iy1 <= iy0:
                continue
            overlap = (ix1 - ix0) * (iy1 - iy0)
            if overlap / t_area >= containment_frac:
                dropped = True
                break
        if not dropped:
            keep.append(t)
    return keep


def extract_page_text(doc: fitz.Document, max_chars_per_page: int = 4000) -> list[str]:
    """Extract text per page as a list of strings (1-indexed: index 0
    is page 1). Truncates each page to `max_chars_per_page` to keep
    downstream prompts bounded for very dense pages. Lossless for
    anything under the cap.
    """
    out: list[str] = []
    for page in doc:
        txt = page.get_text("text") or ""
        if len(txt) > max_chars_per_page:
            txt = txt[:max_chars_per_page] + "\n[…page truncated…]"
        out.append(txt)
    return out


def detect_scanned_pdf(doc: fitz.Document) -> bool:
    """Heuristic: if the whole doc has almost no extractable text AND
    no vector drawings, it's almost certainly a scanned PDF. Caller
    (ingest_document) raises ScannedPdfError so the user gets a clear
    message instead of a silent zero-figure result.
    """
    total_text = 0
    total_drawings = 0
    for page in doc:
        total_text += len(page.get_text("text"))
        if total_text > 400:
            return False
        total_drawings += len(page.get_drawings())
        if total_drawings > 0:
            return False
    return total_text < 400 and total_drawings == 0


def dedup_raster_vector(
    candidates: list[PdfFigureCandidate],
    *,
    containment_frac: float = 0.80,
    raster_min_side_px: int = 200,
) -> list[PdfFigureCandidate]:
    """Dedup rules:

    Per page, for each vector cluster V, look at every raster R on the
    same page whose position we can test. We only know a raster's
    bbox_pt when the caller has set it; in the default pipeline the
    raster extractor returns `bbox_pt=None` (xref metadata doesn't
    carry placement), so this defaults to a no-op and both candidates
    are kept — caption matching will reject the duplicate.

    Explicit containment rule for callers that *do* populate raster
    bbox_pt: raster wins only if its bbox covers ≥ `containment_frac`
    of the vector cluster AND its min-side ≥ `raster_min_side_px`.
    """
    by_page: dict[int, list[PdfFigureCandidate]] = {}
    for c in candidates:
        by_page.setdefault(c.page, []).append(c)

    keep: list[PdfFigureCandidate] = []
    for page, cands in by_page.items():
        vecs = [c for c in cands if c.strategy == "vector"]
        rasters = [c for c in cands if c.strategy == "raster"]
        dropped_vec_ids: set[int] = set()

        for vi, v in enumerate(vecs):
            if v.bbox_pt is None:
                continue
            vx0, vy0, vx1, vy1 = v.bbox_pt
            v_area = max(1.0, (vx1 - vx0) * (vy1 - vy0))
            for r in rasters:
                if r.bbox_pt is None:
                    continue
                rx0, ry0, rx1, ry1 = r.bbox_pt
                ix0 = max(vx0, rx0); iy0 = max(vy0, ry0)
                ix1 = min(vx1, rx1); iy1 = min(vy1, ry1)
                if ix1 <= ix0 or iy1 <= iy0:
                    continue
                overlap = (ix1 - ix0) * (iy1 - iy0)
                if overlap / v_area < containment_frac:
                    continue
                if min(r.width_px, r.height_px) < raster_min_side_px:
                    continue
                dropped_vec_ids.add(vi)
                break

        for i, v in enumerate(vecs):
            if i not in dropped_vec_ids:
                keep.append(v)
        keep.extend(rasters)

    # Stable by (page, strategy priority: raster first, then vector idx).
    return sorted(keep, key=lambda c: (c.page, 0 if c.strategy == "raster" else 1,
                                       c.path.name))
