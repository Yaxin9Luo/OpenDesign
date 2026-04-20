"""Thin pymupdf (fitz) wrappers used by `tools/ingest_document.py` (v1.1).

We treat PDFs as raster sources: Claude's native `document` content block
does the structural extraction (title / sections / figure locations); we
just render pages as PNG locally so the renderer can copy actual figure
pixels into the output artifact.

Kept as a small self-contained util so the tool module stays focused on
the Claude-orchestration logic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import fitz  # pymupdf
from PIL import Image


def page_count(pdf_path: Path) -> int:
    """Number of pages in the PDF."""
    doc = fitz.open(pdf_path)
    try:
        return len(doc)
    finally:
        doc.close()


def render_page_png(pdf_path: Path, page_num: int, out_path: Path,
                    dpi: int = 192) -> tuple[int, int]:
    """Render 1-indexed `page_num` to `out_path` as PNG. Returns (width, height)
    of the rendered image in pixels. dpi=192 ≈ 2× screen density, good for
    Retina previews; bump to 300 for print-destined posters.
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
    """Crop `page_png` to `bbox = (x, y, w, h)` in pixel coords and save as PNG.
    Returns the (width, height) of the cropped image. Clamps bbox to image
    dimensions so a mis-predicted bbox never raises.
    """
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
    """Lightweight metadata probe (bytes + page count + first-page size).
    Used before a full ingest to display in the tool's summary."""
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
