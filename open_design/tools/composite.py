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

from ..util.io import atomic_write_json, sha256_file
from ._contract import ToolContext, obs_error, obs_ok
from ._deck_preview import build_deck_preview_grid
from ._font_embed import build_font_face_css
from .html_renderer import write_html, write_landing_html
from .pptx_renderer import render_slide_preview_png, write_pptx
from ..schema import ArtifactType, CompositionArtifacts, ToolResultRecord
from ..util.logging import log
from ..util.table_png import render_table_png


# v2.2 versioning helpers — every composite call writes into its own
# `composites/iter_<N>/` subdirectory so revise loops + critique iters
# don't lose intermediate state. `final/` symlinks track the latest
# iteration for product consumers (cli display, apply-edits source).


def _open_iter_dir(ctx: ToolContext) -> tuple[Path, int]:
    """Allocate the next composite iteration directory.

    Returns `(iter_dir, iter_num)` where iter_dir = `<run_dir>/composites/iter_NN`.
    Caller writes ALL composite outputs (psd / svg / html / preview / slides)
    into this dir. Use `_refresh_final_links` after a successful write so
    consumers can keep using stable paths via `<run_dir>/final/<name>`.
    """
    iter_num = ctx.next_composite_iter()
    iter_dir = ctx.run_dir / "composites" / f"iter_{iter_num:02d}"
    iter_dir.mkdir(parents=True, exist_ok=True)
    return iter_dir, iter_num


def _prior_preview_sha(ctx: ToolContext) -> str | None:
    """Return the sha256 of the *previous* iteration's preview.png if any
    (lets the new composite's payload encode `supersedes_preview_sha256` so
    DPO training can pair pre/post snapshots).

    Call AFTER `_open_iter_dir` — current composite_iter is N (new dir),
    we want N-1 (the prior dir).
    """
    iter_num = int(ctx.state.get("composite_iter") or 0)
    if iter_num <= 1:
        return None
    prior = ctx.run_dir / "composites" / f"iter_{iter_num - 1:02d}" / "preview.png"
    if not prior.exists():
        return None
    try:
        return sha256_file(prior)
    except OSError:
        return None


def _refresh_final_links(iter_dir: Path, ctx: ToolContext, files: list[str]) -> None:
    """Update `<run_dir>/final/<name>` symlinks to point at this iter's files.

    Existing symlinks (or files) at the target paths are removed first so the
    operation is atomic-ish. Uses RELATIVE symlinks so the run_dir is portable
    if copied to another machine. Skips files that don't exist in iter_dir.
    """
    final_dir = ctx.run_dir / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    for fname in files:
        src = iter_dir / fname
        if not src.exists():
            continue
        link = final_dir / fname
        if link.is_symlink() or link.exists():
            link.unlink()
        # Relative path from final/ to composites/iter_NN/<fname>:
        rel = Path("..") / "composites" / iter_dir.name / fname
        link.symlink_to(rel)
    # Also relink subdirectories (deck/slides/) when present.
    for subdir_name in ("slides",):
        src_sub = iter_dir / subdir_name
        if not src_sub.is_dir():
            continue
        link_sub = final_dir / subdir_name
        if link_sub.is_symlink() or link_sub.exists():
            if link_sub.is_symlink():
                link_sub.unlink()
            else:
                # safety: if it's a real dir, leave it (don't blow away product data)
                continue
        link_sub.symlink_to(Path("..") / "composites" / iter_dir.name / subdir_name)


# Warn when the planner's bbox aspect is this many times off from the
# layer's source-content aspect. Above the threshold we letterbox for
# images / re-render for tables so text/figures stay legible; below,
# we keep the old "stretch to fit" behavior (imperceptible squeeze).
_ASPECT_MISMATCH_WARN_RATIO = 2.0

# Descender-clearance multiplier for text layers. A rasterized Latin glyph
# including descenders occupies ~1.10–1.20 × font_size_px vertically. If a
# planner declares `bbox.h = font_size_px` the descender spills ~20 % below
# the bbox bottom, crashing into any layer directly beneath. Effective
# vertical footprint = max(bbox.h, font_size_px × this multiplier).
_TEXT_DESCENDER_MULTIPLIER = 1.20


# v2.7.5 — per-role, per-slot bboxes mirroring
# `assets/deck_templates/academic-editorial.pptx`. Used by
# `_effective_text_extent` to derive a real bbox for templated text
# layers whose `bbox=None` (positions come from template slots, not
# planner-supplied coords). Keep in sync with `scripts/build_template.py`
# — the renderer is the source of truth for actual placement, so this
# is a detector aid, not a placement authority.
_TEMPLATE_SLOT_BBOX: dict[str, dict[str, tuple[int, int, int, int]]] = {
    "cover": {
        "title": (96, 280, 880, 280),
        "authors": (96, 580, 880, 60),
        "badge": (1660, 80, 180, 40),
        "image_slot": (1000, 0, 920, 1080),
    },
    "section_divider": {
        "section_number": (200, 320, 1520, 50),
        "title": (200, 380, 1520, 200),
        "subtitle": (200, 600, 1520, 60),
    },
    "content": {
        "section_label": (96, 80, 1728, 30),
        "title": (96, 120, 1728, 80),
        "footer": (96, 1020, 1200, 30),
        "slide_number": (1700, 1020, 124, 30),
        "body": (96, 260, 1728, 740),
    },
    "content_with_figure": {
        "section_label": (96, 80, 1728, 30),
        "title": (96, 120, 1728, 80),
        "footer": (96, 1020, 1200, 30),
        "slide_number": (1700, 1020, 124, 30),
        "body": (96, 260, 920, 740),
        "image_slot": (1056, 260, 768, 740),
    },
    "content_with_table": {
        "section_label": (96, 80, 1728, 30),
        "title": (96, 120, 1728, 80),
        "footer": (96, 1020, 1200, 30),
        "slide_number": (1700, 1020, 124, 30),
        "body": (96, 260, 800, 740),
        "table_anchor": (920, 260, 904, 740),
    },
    "closing": {
        "title": (200, 360, 1520, 160),
        "subtitle": (200, 540, 1520, 50),
        "links": (200, 620, 1520, 30),
    },
}


def _slot_bbox(role: str | None, slot: str | None) -> tuple[int, int, int, int] | None:
    """Look up a templated slot's bbox by `(role, slot)`. Returns None if
    the role is unknown or the slot is not in that layout."""
    if not role or not slot:
        return None
    role_map = _TEMPLATE_SLOT_BBOX.get(role)
    if role_map is None:
        return None
    return role_map.get(slot)


def _effective_text_extent(
    layer: Any,
    *,
    role: str | None = None,
) -> tuple[int, int, int, int] | None:
    """Return (x, y, w, h_effective) — the glyph-inclusive vertical footprint
    of a `kind: "text"` layer, or None if bbox/font_size missing.

    Accepts either a dict (poster path: `rendered_layers` records) or a
    Pydantic ``LayerNode`` (deck path: spec-tree children). When the layer
    has no explicit ``bbox`` but does set ``template_slot``, the slot
    bbox is looked up via ``_TEMPLATE_SLOT_BBOX[role][slot]`` so templated
    decks aren't skipped (their child bboxes are intentionally ``None``).

    `bbox.h` is the planner's intent; real rasterized height floors at
    `font_size_px × _TEXT_DESCENDER_MULTIPLIER` so descender collisions
    between stacked text layers surface as real overlaps."""
    kind = layer.get("kind") if isinstance(layer, dict) else getattr(layer, "kind", None)
    if kind != "text":
        return None

    if isinstance(layer, dict):
        bbox = layer.get("bbox")
        slot = layer.get("template_slot")
        fs = layer.get("font_size_px") or 0
    else:
        bbox = getattr(layer, "bbox", None)
        slot = getattr(layer, "template_slot", None)
        fs = getattr(layer, "font_size_px", None) or 0

    bx = by = bw = bh = None
    if bbox is not None:
        try:
            if isinstance(bbox, dict):
                bx = int(bbox.get("x", 0))
                by = int(bbox.get("y", 0))
                bw = int(bbox.get("w", 0))
                bh = int(bbox.get("h", 0))
            else:
                bx = int(getattr(bbox, "x", 0) or 0)
                by = int(getattr(bbox, "y", 0) or 0)
                bw = int(getattr(bbox, "w", 0) or 0)
                bh = int(getattr(bbox, "h", 0) or 0)
        except (TypeError, ValueError):
            bx = by = bw = bh = None

    if bw is None or bw <= 0 or bh is None or bh <= 0:
        slot_bbox = _slot_bbox(role, slot)
        if slot_bbox is None:
            return None
        bx, by, bw, bh = slot_bbox

    try:
        fs = int(fs)
    except (TypeError, ValueError):
        fs = 0
    descender_h = int(fs * _TEXT_DESCENDER_MULTIPLIER) if fs > 0 else 0
    return bx, by, bw, max(bh, descender_h)


def _rects_overlap(a: tuple[int, int, int, int],
                   b: tuple[int, int, int, int]) -> tuple[int, int] | None:
    """Return (x_overlap_px, y_overlap_px) if rects a and b intersect, else None."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x_ov = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    y_ov = max(0, min(ay + ah, by + bh) - max(ay, by))
    if x_ov > 0 and y_ov > 0:
        return x_ov, y_ov
    return None


def _placed_ingest_display_map(
    layers: list[dict[str, Any]],
) -> dict[str, tuple[str, int]]:
    """Map placed ingest figure / table layer_ids to (`"fig"` | `"table"`, N).

    Display numbers follow the order each `ingest_fig_NN` / `ingest_table_NN`
    appears in the sorted layer list (same order the poster reads
    top-to-bottom once z_index is respected). The 01/02/… suffix from the
    ingest step is intentionally NOT reused as the display number — the
    paper's Fig. 7 might be poster Fig. 2 if that's the order the planner
    chose to lay them out.
    """
    out: dict[str, tuple[str, int]] = {}
    fig_n = 0
    tbl_n = 0
    for L in layers:
        lid = L.get("layer_id") or ""
        kind = L.get("kind")
        if kind == "image" and lid.startswith("ingest_fig_"):
            fig_n += 1
            out[lid] = ("fig", fig_n)
        elif kind == "table" and lid.startswith("ingest_table_"):
            tbl_n += 1
            out[lid] = ("table", tbl_n)
    return out


def _detect_missing_figure_xrefs(
    layers: list[dict[str, Any]],
    spec: Any,
) -> list[str]:
    """Return layer_ids of placed `ingest_fig_NN` / `ingest_table_NN` that no
    text layer cross-references via `(Fig. N)` / `(Table N)` literal.

    Skips entirely for non-paper posters (no placed ingest layers). A layer
    counts as cross-referenced when ANY text layer's `.text` contains the
    literal `Fig. N` / `Figure N` / `Table N` pattern (case-insensitive,
    period-optional) for its display number. This is the poster-body's
    "as shown in Fig. 2" reference that signals the viewer where to look.
    """
    display_map = _placed_ingest_display_map(layers)
    if not display_map:
        return []

    import re

    haystack_parts: list[str] = []
    for L in layers:
        if L.get("kind") != "text":
            continue
        t = L.get("text") or ""
        if t:
            haystack_parts.append(t)
    # Pull from the authoritative DesignSpec too — covers cases where
    # render_text_layer hasn't yet populated `text` onto rendered_layers
    # but the planner's layer_graph has it.
    for node in list(getattr(spec, "layer_graph", None) or []):
        if getattr(node, "kind", None) != "text":
            continue
        t = getattr(node, "text", None) or ""
        if t:
            haystack_parts.append(t)
    haystack = "\n".join(haystack_parts)

    misses: list[str] = []
    for layer_id, (kind, n) in display_map.items():
        if kind == "fig":
            pattern = rf"\b(?:fig(?:ure)?\.?)\s*{n}\b"
        else:
            pattern = rf"\btable\.?\s*{n}\b"
        if not re.search(pattern, haystack, re.IGNORECASE):
            misses.append(layer_id)
    return misses


def _detect_text_overlaps(layers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Detect glyph-inclusive bbox collisions between poster text layers.

    Emits one `composite.text_overlap_warning` log event per colliding pair
    and returns the list for inclusion in the `obs_ok` summary — which the
    planner reads on the next turn, so the collision feeds back without
    waiting for a full critique pass.
    """
    text_layers = [
        (L, _effective_text_extent(L))
        for L in layers
        if L.get("kind") == "text"
    ]
    text_layers = [(L, ext) for L, ext in text_layers if ext is not None]
    warnings: list[dict[str, Any]] = []
    for i in range(len(text_layers)):
        la, ea = text_layers[i]
        for j in range(i + 1, len(text_layers)):
            lb, eb = text_layers[j]
            ov = _rects_overlap(ea, eb)
            if ov is None:
                continue
            _x_ov, y_ov = ov
            entry = {
                "layer_a": la.get("layer_id"),
                "layer_b": lb.get("layer_id"),
                "y_overlap_px": int(y_ov),
                "font_size_a": int(la.get("font_size_px") or 0),
                "font_size_b": int(lb.get("font_size_px") or 0),
            }
            warnings.append(entry)
            log("composite.text_overlap_warning", **entry)
    return warnings


def _node_bbox(node: Any, role: str | None,
               slide_w: int, slide_h: int) -> tuple[int, int, int, int] | None:
    """Resolve a templated-deck child's effective bbox.

    Order of precedence:
      1. Explicit ``node.bbox`` (planner-supplied absolute coords).
      2. ``_TEMPLATE_SLOT_BBOX[role][template_slot]`` lookup.

    Returns None when neither source produces a positive-area rect — the
    caller treats that as "this layer has no resolvable position" (the
    very condition the v2.7.5 detector flags)."""
    bbox = getattr(node, "bbox", None)
    if bbox is not None:
        try:
            bx = int(getattr(bbox, "x", 0) or 0)
            by = int(getattr(bbox, "y", 0) or 0)
            bw = int(getattr(bbox, "w", 0) or 0)
            bh = int(getattr(bbox, "h", 0) or 0)
        except (TypeError, ValueError):
            bx = by = bw = bh = 0
        if bw > 0 and bh > 0:
            return bx, by, bw, bh

    slot_bbox = _slot_bbox(role, getattr(node, "template_slot", None))
    if slot_bbox is not None:
        return slot_bbox

    return None


def _detect_deck_text_overlaps(
    slides: list[Any],
    *,
    slide_w: int,
    slide_h: int,
) -> list[dict[str, Any]]:
    """Walk each slide's text children + flag layout regressions.

    Three classes of warning:
      - ``slot_collision`` (blocker): two text children share the same
        ``template_slot`` on the same slide. The renderer's
        ``replace_text_in_shape`` is last-write-wins, so one child's text
        silently disappears unless the planner spreads them across slots.
      - ``unanchored_text`` (blocker): a templated-deck text child has
        ``bbox=None`` AND ``template_slot=None`` (or a slot the layout
        doesn't expose). The renderer's fallback dumps it into a
        full-slide textbox at (0,0,slide_w,slide_h), which is exactly
        the v2.7.5 "Table 2 caption at the top of the page" defect.
      - ``text_overlaps_shape`` (high): an effective text bbox overlaps a
        non-text sibling (image / table / callout). Catches captions
        landing on top of figures.

    Pure: never mutates ``slides``. Emits structured log events; returns
    the warning list so ``_composite_deck`` can roll it into the payload
    the planner sees on the next turn.
    """
    warnings: list[dict[str, Any]] = []
    for slide in slides:
        slide_id = getattr(slide, "layer_id", None)
        role = getattr(slide, "role", None) or "content"
        children = list(getattr(slide, "children", None) or [])

        text_children: list[tuple[Any, tuple[int, int, int, int] | None]] = []
        nontext_children: list[tuple[Any, tuple[int, int, int, int] | None]] = []
        slot_seen: dict[str, str] = {}

        for child in children:
            kind = getattr(child, "kind", None)
            slot = getattr(child, "template_slot", None)
            cid = getattr(child, "layer_id", None) or "?"

            if kind == "text":
                if slot:
                    prior = slot_seen.get(slot)
                    if prior:
                        entry = {
                            "kind": "slot_collision",
                            "severity": "blocker",
                            "slide_id": slide_id,
                            "template_slot": slot,
                            "layer_a": prior,
                            "layer_b": cid,
                        }
                        warnings.append(entry)
                        log("composite.deck_text_overlap_warning", **entry)
                    else:
                        slot_seen[slot] = cid

                ext = _effective_text_extent(child, role=role)
                if ext is None:
                    if getattr(child, "bbox", None) is None and not slot:
                        entry = {
                            "kind": "unanchored_text",
                            "severity": "blocker",
                            "slide_id": slide_id,
                            "layer_id": cid,
                            "text_preview": (
                                (getattr(child, "text", None) or "")[:80]
                            ),
                        }
                        warnings.append(entry)
                        log("composite.deck_text_overlap_warning", **entry)
                    continue
                text_children.append((child, ext))
            elif kind in ("image", "table", "callout", "background"):
                if kind == "background":
                    continue  # backgrounds are full-bleed by design
                bbox = _node_bbox(child, role, slide_w, slide_h)
                nontext_children.append((child, bbox))

        # Text vs text — same-slide stacking collisions.
        for i in range(len(text_children)):
            la, ea = text_children[i]
            for j in range(i + 1, len(text_children)):
                lb, eb = text_children[j]
                ov = _rects_overlap(ea, eb)
                if ov is None:
                    continue
                _x_ov, y_ov = ov
                entry = {
                    "kind": "text_overlaps_text",
                    "severity": "high",
                    "slide_id": slide_id,
                    "layer_a": getattr(la, "layer_id", None),
                    "layer_b": getattr(lb, "layer_id", None),
                    "y_overlap_px": int(y_ov),
                }
                warnings.append(entry)
                log("composite.deck_text_overlap_warning", **entry)

        # Text vs non-text (image / table / callout).
        for la, ea in text_children:
            for lb, eb in nontext_children:
                if eb is None:
                    continue
                ov = _rects_overlap(ea, eb)
                if ov is None:
                    continue
                x_ov, y_ov = ov
                entry = {
                    "kind": "text_overlaps_shape",
                    "severity": "high",
                    "slide_id": slide_id,
                    "text_layer": getattr(la, "layer_id", None),
                    "shape_layer": getattr(lb, "layer_id", None),
                    "shape_kind": getattr(lb, "kind", None),
                    "x_overlap_px": int(x_ov),
                    "y_overlap_px": int(y_ov),
                }
                warnings.append(entry)
                log("composite.deck_text_overlap_warning", **entry)

    return warnings


def _detect_orphan_callouts(
    slides: list[Any],
    *,
    slide_w: int,
    slide_h: int,
) -> tuple[set[str], list[dict[str, Any]]]:
    """Identify callout layer_ids that should be dropped at composite time.

    A callout is an orphan when:
      - ``anchor_layer_id`` is None or empty, OR
      - ``anchor_layer_id`` references a sibling that doesn't exist on the
        same slide, OR
      - the referenced sibling is not a placeable shape (only
        ``image`` / ``table`` qualify), OR
      - both ``anchor_layer_id`` AND ``callout_region`` are set but the
        region's bbox does not intersect the anchor's bbox (the v2.7.5
        "circle floating in empty space" defect — slide16 of the
        2026-04-26 dogfood).

    Pure inspection over the spec tree — does NOT call into the renderer.
    Returns ``(orphan_layer_ids, warnings)``; the renderer's pass-2
    callout walker honours the set by skipping placement entirely.
    """
    orphans: set[str] = set()
    warnings: list[dict[str, Any]] = []

    for slide in slides:
        slide_id = getattr(slide, "layer_id", None)
        role = getattr(slide, "role", None) or "content"
        children = list(getattr(slide, "children", None) or [])
        sibling_by_id: dict[str, Any] = {}
        for child in children:
            cid = getattr(child, "layer_id", None)
            if cid:
                sibling_by_id[cid] = child

        for child in children:
            if getattr(child, "kind", None) != "callout":
                continue
            cid = getattr(child, "layer_id", None) or "?"
            anchor_id = getattr(child, "anchor_layer_id", None)
            reason: str | None = None

            if not anchor_id:
                reason = "no_anchor_layer_id"
            else:
                anchor = sibling_by_id.get(anchor_id)
                if anchor is None:
                    reason = "anchor_not_on_slide"
                elif getattr(anchor, "kind", None) not in ("image", "table"):
                    reason = "anchor_kind_not_placeable"
                else:
                    region = getattr(child, "callout_region", None)
                    if region is not None:
                        anchor_bbox = _node_bbox(anchor, role, slide_w, slide_h)
                        if anchor_bbox is not None:
                            try:
                                rx = int(getattr(region, "x", 0) or 0)
                                ry = int(getattr(region, "y", 0) or 0)
                                rw = int(getattr(region, "w", 0) or 0)
                                rh = int(getattr(region, "h", 0) or 0)
                            except (TypeError, ValueError):
                                rx = ry = rw = rh = 0
                            if rw > 0 and rh > 0 and _rects_overlap(
                                (rx, ry, rw, rh), anchor_bbox
                            ) is None:
                                reason = "region_outside_anchor_bbox"

            if reason is not None:
                orphans.add(cid)
                entry = {
                    "slide_id": slide_id,
                    "callout_layer_id": cid,
                    "anchor_layer_id": anchor_id,
                    "reason": reason,
                }
                warnings.append(entry)
                log("composite.callout_orphan_warning", **entry)

    return orphans, warnings


# v2.8.2 C3 — closing slot enforcer. Last slide of a deck must contain
# substantive takeaways, not the template-default "Thank You" stub.
# Detection only — emits a warning to the planner via tool_result so it
# can populate the closing slide on the next iteration. No auto-fix.
#
# Same blacklist as v2.8.2 C1 sanitizer; kept local to avoid cross-module
# import in case C1 hasn't merged yet.
_CLOSING_STUB_PHRASES: tuple[str, ...] = (
    "thank you",
    "thanks",
    "questions",
    "q&a",
    "q & a",
    "any questions",
)

_CLOSING_PLACEHOLDER_SUBSTRINGS: tuple[str, ...] = (
    "paper title goes here",
    "author one",
    "author two",
    "affiliation goes here",
    "yyyy-mm-dd",
    "your name here",
)


def _collect_closing_text_runs(node: Any) -> list[str]:
    """Returns all non-empty text runs from a slide subtree.

    Walks ``text`` and ``caption`` on the node and recurses into
    ``children``. Empty / whitespace-only runs are filtered out.
    """
    runs: list[str] = []
    text = getattr(node, "text", None)
    if text and str(text).strip():
        runs.append(str(text).strip())
    caption = getattr(node, "caption", None)
    if caption and str(caption).strip():
        runs.append(str(caption).strip())
    for child in (getattr(node, "children", None) or []):
        runs.extend(_collect_closing_text_runs(child))
    return runs


def _find_closing_slide(spec: Any) -> Any | None:
    """Returns the closing slide LayerNode by ``role="closing"`` (preferred)
    or the last slide (``kind="slide"``) in ``layer_graph`` (fallback).
    Returns None if no slides found.
    """
    layer_graph = getattr(spec, "layer_graph", None) or []
    closing = None
    last_slide = None
    for node in layer_graph:
        if getattr(node, "kind", None) != "slide":
            continue
        last_slide = node
        if getattr(node, "role", None) == "closing":
            closing = node
    return closing or last_slide


def _detect_closing_stub(spec: Any) -> list[dict[str, Any]]:
    """Returns warnings if the last slide's content is too thin or stub-like.

    Stub criteria (any one triggers warning):
    - Fewer than 3 non-empty text runs across all descendants
    - All runs match ``_CLOSING_STUB_PHRASES`` (e.g. just "Thank you" / "Q&A")
    - All runs match ``_CLOSING_PLACEHOLDER_SUBSTRINGS``

    Operates on structural properties only — no per-paper heuristics, so
    the check generalizes across paper / blog / .docx / free-text decks.
    Returns ``[]`` when the closing slide is substantive (the common case).
    """
    closing = _find_closing_slide(spec)
    if closing is None:
        return []
    runs = _collect_closing_text_runs(closing)
    warnings: list[dict[str, Any]] = []
    slide_id = (
        getattr(closing, "layer_id", None)
        or getattr(closing, "name", None)
        or "<closing>"
    )
    if len(runs) < 3:
        entry = {
            "slide_id": slide_id,
            "reason": "thin_content",
            "text_run_count": len(runs),
            "preview": runs[:3],
        }
        warnings.append(entry)
        log("composite.closing_stub_warning", **entry)
        return warnings
    lower_runs = [r.lower() for r in runs]
    all_stub = all(
        any(needle in r for needle in _CLOSING_STUB_PHRASES) for r in lower_runs
    )
    if all_stub:
        entry = {
            "slide_id": slide_id,
            "reason": "all_stub_phrases",
            "text_run_count": len(runs),
            "preview": runs[:3],
        }
        warnings.append(entry)
        log("composite.closing_stub_warning", **entry)
        return warnings
    all_placeholder = all(
        any(needle in r for needle in _CLOSING_PLACEHOLDER_SUBSTRINGS)
        for r in lower_runs
    )
    if all_placeholder:
        entry = {
            "slide_id": slide_id,
            "reason": "all_placeholders",
            "text_run_count": len(runs),
            "preview": runs[:3],
        }
        warnings.append(entry)
        log("composite.closing_stub_warning", **entry)
    return warnings


def _aspect_fit_contain(
    src_size: tuple[int, int],
    dst_size: tuple[int, int],
) -> tuple[int, int, int, int]:
    """Compute the (new_w, new_h, off_x, off_y) that fits `src_size`
    into `dst_size` preserving aspect ratio, centered. Letterbox-style.

    Empty source or dest yields a 1×1 no-op at origin so callers don't
    crash on malformed input.
    """
    sw, sh = src_size
    dw, dh = dst_size
    if sw <= 0 or sh <= 0 or dw <= 0 or dh <= 0:
        return 1, 1, 0, 0
    scale = min(dw / sw, dh / sh)
    nw = max(1, int(sw * scale))
    nh = max(1, int(sh * scale))
    off_x = (dw - nw) // 2
    off_y = (dh - nh) // 2
    return nw, nh, off_x, off_y


def _maybe_warn_aspect(layer: dict[str, Any], src_size: tuple[int, int],
                       bbox: tuple[int, int, int, int]) -> None:
    """Emit `composite.bbox_aspect_warning` when the planner's bbox
    aspect ratio diverges from the layer's source content by more than
    `_ASPECT_MISMATCH_WARN_RATIO`. Future planner-prompt tuning can
    consume these warnings to learn which figure kinds get systemically
    under-sized."""
    sw, sh = src_size
    _bx, _by, bw, bh = bbox
    if min(sw, sh, bw, bh) <= 0:
        return
    src_aspect = sw / sh
    bbox_aspect = bw / bh
    ratio = max(src_aspect, bbox_aspect) / min(src_aspect, bbox_aspect)
    if ratio >= _ASPECT_MISMATCH_WARN_RATIO:
        log("composite.bbox_aspect_warning",
            layer_id=layer.get("layer_id"),
            kind=layer.get("kind"),
            src_size=f"{sw}x{sh}",
            bbox=f"{bw}x{bh}",
            aspect_mismatch=round(ratio, 2))


def composite(args: dict[str, Any], *, ctx: ToolContext) -> ToolResultRecord:
    spec = ctx.state.get("design_spec")
    if spec is None:
        return obs_error("propose_design_spec must be called first", category="validation")

    # Landing mode (v1.0 #8) is HTML-only — no PSD/SVG, no per-layer PNGs.
    # It reads the section tree directly from design_spec.layer_graph.
    if spec.artifact_type == ArtifactType.LANDING:
        return _composite_landing(spec, ctx)

    # Deck mode (v1.0 #7) is PPTX-primary with per-slide PNG previews.
    # Reads the slide tree from design_spec.layer_graph (kind="slide"); inline
    # images inside slides are hydrated from ctx.state["rendered_layers"].
    if spec.artifact_type == ArtifactType.DECK:
        return _composite_deck(spec, ctx)

    rendered = ctx.state["rendered_layers"]
    if not rendered:
        return obs_error(
            "no layers rendered yet — call generate_background and render_text_layer first",
            category="validation",
        )

    canvas = spec.canvas
    cw, ch = int(canvas["w_px"]), int(canvas["h_px"])

    # v1.1 paper2any: ingested PDF figures are registered in rendered_layers
    # with bbox=None (they were authored for flow-layout landing/deck use).
    # The planner places them on the poster by giving each a bbox in
    # spec.layer_graph. Hydrate that bbox onto the rendered_layer record
    # before composite walks it. Pattern mirrors _hydrate_landing_image_srcs.
    _hydrate_poster_layer_bboxes(rendered, spec)

    sorted_layers = sorted(rendered.values(), key=lambda L: int(L.get("z_index", 0)))
    # Drop any image/background layers that still have no bbox — the planner
    # declared them in spec but didn't place them, OR they're stale records.
    sorted_layers = [L for L in sorted_layers if L.get("bbox")]

    iter_dir, iter_num = _open_iter_dir(ctx)
    prior_preview_sha = _prior_preview_sha(ctx)
    psd_path = iter_dir / "poster.psd"
    svg_path = iter_dir / "poster.svg"
    html_path = iter_dir / "poster.html"
    preview_path = iter_dir / "preview.png"

    layer_manifest: list[dict[str, Any]] = []

    try:
        _write_psd(sorted_layers, cw, ch, psd_path, layer_manifest, ctx)
    except Exception as e:
        return obs_error(f"PSD write failed: {e}", category="api")

    try:
        _write_svg(sorted_layers, cw, ch, svg_path, ctx)
    except Exception as e:
        return obs_error(f"SVG write failed: {e}", category="api")

    try:
        write_html(sorted_layers, cw, ch, html_path, ctx)
    except Exception as e:
        return obs_error(f"HTML write failed: {e}", category="api")

    try:
        _write_preview(sorted_layers, cw, ch, preview_path, ctx)
    except Exception as e:
        return obs_error(f"preview render failed: {e}", category="api")

    text_overlap_warnings = _detect_text_overlaps(sorted_layers)
    xref_misses = _detect_missing_figure_xrefs(sorted_layers, spec)

    artifacts = CompositionArtifacts(
        psd_path=str(psd_path),
        svg_path=str(svg_path),
        html_path=str(html_path),
        preview_path=str(preview_path),
        layer_manifest=layer_manifest,
    )
    ctx.state["composition"] = artifacts
    _refresh_final_links(iter_dir, ctx,
                         ["poster.psd", "poster.svg", "poster.html", "preview.png"])
    log("composite.done",
        iter=iter_num,
        psd=str(psd_path), svg=str(svg_path), html=str(html_path),
        preview=str(preview_path), layers=len(sorted_layers),
        text_overlaps=len(text_overlap_warnings),
        figure_xref_misses=len(xref_misses))

    preview_sha = sha256_file(preview_path)
    payload: dict[str, Any] = {
        "artifact_type": "poster",
        "iteration": iter_num,
        "preview_sha256": preview_sha,
        "psd_sha256": sha256_file(psd_path),
        "svg_sha256": sha256_file(svg_path),
        "html_sha256": sha256_file(html_path),
        "n_layers": len(sorted_layers),
        "canvas": {"w_px": cw, "h_px": ch},
        # Versioned paths: each iteration's outputs survive on disk for
        # DPO / layered-gen training. Use this relative_path; final/ is
        # only a convenience symlink for product consumers.
        "preview_relative_path": f"composites/iter_{iter_num:02d}/preview.png",
        "html_relative_path": f"composites/iter_{iter_num:02d}/poster.html",
        # Real environment state — text overlaps and missing xrefs are
        # actual quality signals. The policy can decide whether to fix
        # them via edit_layer or move on. NOT prose hints.
        "text_overlap_warnings": text_overlap_warnings,
        "xref_misses": xref_misses,
    }
    if prior_preview_sha:
        payload["supersedes_preview_sha256"] = prior_preview_sha
    return obs_ok(payload)


def _composite_landing(spec: Any, ctx: ToolContext) -> ToolResultRecord:
    """HTML-only landing-mode composite. Reads the section tree from
    design_spec.layer_graph (not ctx.state['rendered_layers'])."""
    layer_graph = list(spec.layer_graph or [])
    if not layer_graph:
        return obs_error(
            "landing design_spec has empty layer_graph — "
            "propose_design_spec with a section tree first",
            category="validation",
        )

    iter_dir, iter_num = _open_iter_dir(ctx)
    prior_preview_sha = _prior_preview_sha(ctx)
    html_path = iter_dir / "index.html"
    preview_path = iter_dir / "preview.png"
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
        elif kind == "table":
            manifest.append({
                "layer_id": node.layer_id,
                "name": node.name,
                "kind": "table",
                "src_path": node.src_path,
                "rows": list(node.rows or []),
                "headers": list(node.headers or []),
                "col_highlight_rule": list(node.col_highlight_rule or []),
                "caption": node.caption or "",
            })

    try:
        write_landing_html(spec, html_path, ctx)
    except Exception as e:
        return obs_error(f"landing HTML write failed: {e}", category="api")

    try:
        _write_landing_preview(spec, preview_path, ctx)
    except Exception as e:
        return obs_error(f"landing preview render failed: {e}", category="api")

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

    _refresh_final_links(iter_dir, ctx, ["index.html", "preview.png"])
    preview_sha = sha256_file(preview_path)
    payload: dict[str, Any] = {
        "artifact_type": "landing",
        "iteration": iter_num,
        "preview_sha256": preview_sha,
        "html_sha256": sha256_file(html_path),
        "n_sections": section_ct,
        "n_images": image_ct,
        "canvas_width_px": cw,
        "preview_relative_path": f"composites/iter_{iter_num:02d}/preview.png",
        "html_relative_path": f"composites/iter_{iter_num:02d}/index.html",
    }
    if prior_preview_sha:
        payload["supersedes_preview_sha256"] = prior_preview_sha
    return obs_ok(payload)


def _composite_deck(spec: Any, ctx: ToolContext) -> ToolResultRecord:
    """PPTX-primary deck composite. Reads the slide tree from
    design_spec.layer_graph (top-level `kind="slide"` nodes). Writes:
      - deck.pptx — native PowerPoint file (editable TextFrames)
      - slides/slide_<i>.png — per-slide Pillow preview thumbs
      - preview.png — grid thumb of the slides (for chat UX + critic)
    """
    layer_graph = list(spec.layer_graph or [])
    slides = [n for n in layer_graph if getattr(n, "kind", None) == "slide"]
    if not slides:
        return obs_error(
            "deck design_spec has no slides — propose_design_spec with a "
            "layer_graph containing at least one kind=\"slide\" node first",
            category="validation",
        )

    # v2.7.2 — apply section_number policy BEFORE hydration / write so the
    # renderer sees a consistent, monotonic numbering. `apply_section_policy`
    # is pure: it returns new LayerNode copies without mutating the spec.
    # Splice the post-policy slides back into the spec's layer_graph in the
    # same positions so write_pptx walks the renumbered nodes. Non-slide
    # entries pass through untouched.
    from ..util.section_renumber import apply_section_policy
    policy = getattr(ctx.settings, "section_number_policy", "renumber")
    renumbered = apply_section_policy(slides, policy)
    rebuilt: list[Any] = []
    slide_iter = iter(renumbered)
    for node in layer_graph:
        if getattr(node, "kind", None) == "slide":
            rebuilt.append(next(slide_iter))
        else:
            rebuilt.append(node)
    spec.layer_graph = rebuilt
    slides = renumbered

    # Hydrate inline images inside slides (same pattern as landing — planner
    # may declare image children separately and call generate_image later).
    _hydrate_deck_image_srcs(slides, ctx)

    iter_dir, iter_num = _open_iter_dir(ctx)
    prior_preview_sha = _prior_preview_sha(ctx)
    pptx_path = iter_dir / "deck.pptx"
    slides_dir = iter_dir / "slides"
    slides_dir.mkdir(parents=True, exist_ok=True)
    preview_path = iter_dir / "preview.png"

    canvas = spec.canvas or {}
    slide_w = int(canvas.get("w_px") or 1920)
    slide_h = int(canvas.get("h_px") or 1080)

    # v2.7 — provenance audit BEFORE write_pptx. When the deck has an
    # ingested paper source, every body text layer carrying a numeric
    # token must have an `evidence_quote` matching the ingest text.
    # Strict mode: replace unverified numbers with [?] markers. Empty
    # ingest list → no-op (free-text decks unaffected). Report persisted
    # alongside artifacts for human inspection.
    pv_failures = 0
    if ctx.state.get("ingested"):
        from ..util.provenance import apply_strict_provenance, validate_provenance
        pv_report = validate_provenance(spec, ctx)
        if pv_report.has_failures():
            pv_failures = len(pv_report.failures)
            n_mut = apply_strict_provenance(spec, pv_report)
            log("composite.deck.provenance_fail",
                n_failures=pv_failures, n_mutated=n_mut,
                failure_ids=[f.layer_id for f in pv_report.failures],
                failure_reasons=[f.reason for f in pv_report.failures])
        else:
            log("composite.deck.provenance_ok",
                n_audited=pv_report.n_text_layers_audited,
                n_with_numbers=pv_report.n_layers_with_numbers)
        atomic_write_json(iter_dir / "provenance_report.json",
                          pv_report.to_dict())

    # v2.7.5 — quarantine orphan callouts BEFORE write_pptx so the
    # renderer never sees a callout pointing at empty space. The
    # renderer also re-checks via `ctx.state["orphan_callouts"]` in
    # pass-2 so the gate holds even if a downstream caller bypasses
    # this composite entry point.
    orphan_callout_ids, orphan_callout_warnings = _detect_orphan_callouts(
        slides, slide_w=slide_w, slide_h=slide_h,
    )
    ctx.state["orphan_callouts"] = orphan_callout_ids

    # v2.8.2-C1 — strip placeholder text + debug-named empty shapes BEFORE
    # write_pptx. The orphan-callout pass above only catches callouts that
    # point at empty space; this pass catches callouts that *do* anchor a
    # real region but never had their label rewritten ("Annotation 12",
    # ``callout_05_a`` with empty text), plus title/body leaks like
    # "Paper Title Goes Here" / "arxiv.org/abs/XXXX". Operates on
    # structural properties only — no per-paper heuristics.
    from ..util.export_sanitizer import sanitize_design_spec
    spec, sanitizer_warnings = sanitize_design_spec(spec)
    ctx.state["sanitizer_warnings"] = sanitizer_warnings
    # Refresh `slides` from the sanitized spec so downstream detectors,
    # write_pptx, and the manifest builder all walk the cleaned tree.
    slides = [n for n in spec.layer_graph if getattr(n, "kind", None) == "slide"]

    # v2.7.5 — deck-side text-overlap detector. Templated decks
    # intentionally have ``bbox=None`` on every child (positions come
    # from template slots), so the poster-only `_detect_text_overlaps`
    # was dead weight here. The deck variant resolves slot bboxes via
    # `_TEMPLATE_SLOT_BBOX[role][slot]`, then flags slot collisions /
    # unanchored text / text-overlapping-shape — the three failure
    # modes the 2026-04-26 longcat-next dogfood exhibited.
    deck_text_overlaps = _detect_deck_text_overlaps(
        slides, slide_w=slide_w, slide_h=slide_h,
    )

    # v2.8.2-C2 — naive title-body alignment validator. Detects slides where
    # the title makes a promise the body/figure doesn't deliver (e.g. title
    # "Training Stage Ablations" but body shows training config with no
    # ablation data). Set-overlap of noun-phrase tokens; no embeddings, no
    # LLM call. Threshold-based warnings only — planner self-corrects on
    # next iteration. Generalizes across paper / blog / .docx etc.
    from ..util.slide_alignment import detect_alignment_warnings
    alignment_warnings = detect_alignment_warnings(spec)
    ctx.state["alignment_warnings"] = alignment_warnings

    # v2.8.2 C3 — closing slot enforcer. Warn the planner when the last
    # slide is the template-default "Thank You" stub (or thin / placeholder
    # content). Detection only — the renderer continues with the existing
    # spec; the warning surfaces in the tool_result payload so the planner
    # can populate real takeaways on the next iteration.
    closing_warnings = _detect_closing_stub(spec)
    ctx.state["closing_warnings"] = closing_warnings

    try:
        slide_count = write_pptx(spec, pptx_path, ctx)
    except Exception as e:
        return obs_error(f"PPTX write failed: {e}", category="api")

    slide_pngs: list[Path] = []
    for idx, slide_node in enumerate(slides):
        png_path = slides_dir / f"slide_{idx:02d}.png"
        try:
            render_slide_preview_png(slide_node, slide_w, slide_h, png_path, ctx)
            slide_pngs.append(png_path)
        except Exception as e:
            return obs_error(f"slide {idx} preview render failed: {e}", category="api")

    try:
        build_deck_preview_grid(slide_pngs, preview_path)
    except Exception as e:
        return obs_error(f"deck preview grid failed: {e}", category="api")

    manifest: list[dict[str, Any]] = []
    for idx, slide_node in enumerate(slides):
        entry = {
            "layer_id": slide_node.layer_id,
            "name": slide_node.name,
            "kind": "slide",
            "index": idx,
            "children": [
                {
                    "layer_id": c.layer_id,
                    "name": c.name,
                    "kind": c.kind,
                    "text": getattr(c, "text", None),
                    "src_path": getattr(c, "src_path", None),
                }
                for c in (slide_node.children or [])
            ],
        }
        manifest.append(entry)

    artifacts = CompositionArtifacts(
        psd_path=None,
        svg_path=None,
        html_path=None,
        pptx_path=str(pptx_path),
        preview_path=str(preview_path),
        layer_manifest=manifest,
    )
    ctx.state["composition"] = artifacts

    image_ct = sum(
        1 for s in slides
        for c in (getattr(s, "children", None) or [])
        if getattr(c, "kind", None) in ("image", "background")
        and getattr(c, "src_path", None)
    )
    log("composite.deck.done",
        pptx=str(pptx_path), preview=str(preview_path),
        slides=slide_count, images=image_ct,
        text_overlaps=len(deck_text_overlaps),
        orphan_callouts=len(orphan_callout_warnings),
        alignment_warnings=len(alignment_warnings),
        closing_warnings=len(closing_warnings))

    _refresh_final_links(iter_dir, ctx, ["deck.pptx", "preview.png"])
    preview_sha = sha256_file(preview_path)
    payload: dict[str, Any] = {
        "artifact_type": "deck",
        "iteration": iter_num,
        "preview_sha256": preview_sha,
        "pptx_sha256": sha256_file(pptx_path),
        "n_slides": slide_count,
        "n_images": image_ct,
        "canvas": {"w_px": slide_w, "h_px": slide_h},
        "preview_relative_path": f"composites/iter_{iter_num:02d}/preview.png",
        "pptx_relative_path": f"composites/iter_{iter_num:02d}/deck.pptx",
        # v2.7.5 — real environment signals the planner reads on the
        # next turn. Empty lists mean a clean render; non-empty means
        # the planner should fix slot wiring (text overlaps) or drop
        # un-anchorable callouts before re-composing.
        "text_overlap_warnings": deck_text_overlaps,
        "orphan_callout_warnings": orphan_callout_warnings,
        # v2.8.2-C1 — placeholder + debug-named-empty shapes the export
        # sanitizer dropped before write_pptx. Empty list = clean spec.
        "sanitizer_warnings": sanitizer_warnings,
        # v2.8.2-C2 — naive set-overlap signal. Slides whose title noun
        # phrases don't appear in the body/figure text. Empty = clean.
        "alignment_warnings": alignment_warnings,
        # v2.8.2 C3 — empty list means the closing slide carries real
        # takeaways; non-empty means the planner left the template stub
        # ("Thank You" / "Q&A") and should populate it on the next pass.
        "closing_warnings": closing_warnings,
    }
    if prior_preview_sha:
        payload["supersedes_preview_sha256"] = prior_preview_sha
    return obs_ok(payload)


def _hydrate_poster_layer_bboxes(rendered: dict[str, dict[str, Any]],
                                 spec: Any) -> None:
    """Copy bbox from spec.layer_graph onto rendered_layers records that
    lack one — poster-specific companion to the landing/deck hydration.

    Ingested PDF figures (v1.1 paper2any) register with bbox=None since they
    have no intrinsic placement — the planner chooses where to put each
    figure on the poster canvas by giving it a bbox inside its
    `propose_design_spec` call. Without this hydration, the poster PSD/SVG
    writers crash on `None["x"]`.

    The spec is authoritative for placement; rendered_layers is authoritative
    for content. We merge by layer_id.
    """
    for node in (spec.layer_graph or []):
        nb = getattr(node, "bbox", None)
        if nb is None:
            continue
        lid = getattr(node, "layer_id", None)
        if lid is None or lid not in rendered:
            continue
        rec = rendered[lid]
        if rec.get("bbox"):
            continue
        try:
            bbox_dict = {"x": int(nb.x), "y": int(nb.y),
                         "w": int(nb.w), "h": int(nb.h)}
            if nb.purpose is not None:
                bbox_dict["purpose"] = nb.purpose
        except AttributeError:
            continue
        rec["bbox"] = bbox_dict
        # Promote z_index from spec if rendered record didn't have one.
        if "z_index" in rec and rec["z_index"] == 0:
            spec_z = getattr(node, "z_index", None)
            if spec_z is not None:
                rec["z_index"] = int(spec_z)


def _hydrate_deck_image_srcs(slides: list[Any], ctx: ToolContext) -> None:
    """Copy src_path from rendered_layers onto each slide's image/background
    children. Mirrors `_hydrate_landing_image_srcs` — see that docstring.
    """
    rendered = ctx.state.get("rendered_layers") or {}
    if not rendered:
        return
    for slide in slides:
        children = list(getattr(slide, "children", None) or [])
        new_children: list[Any] = []
        changed = False
        for child in children:
            kind = getattr(child, "kind", None)
            if kind not in ("image", "background", "table"):
                new_children.append(child)
                continue
            # Tables carry structured rows/headers too — hydrate those
            # alongside src_path, same pattern as image aspect_ratio.
            needs_src = not getattr(child, "src_path", None)
            needs_rows = (kind == "table"
                          and not (getattr(child, "rows", None)
                                   or getattr(child, "headers", None)))
            if not needs_src and not needs_rows:
                new_children.append(child)
                continue
            rec = rendered.get(getattr(child, "layer_id", None))
            if rec and rec.get("src_path"):
                updates: dict[str, Any] = {}
                if needs_src:
                    updates["src_path"] = rec["src_path"]
                    updates["aspect_ratio"] = (rec.get("aspect_ratio")
                                               or getattr(child, "aspect_ratio", None))
                if kind == "table":
                    updates.setdefault("rows", rec.get("rows") or [])
                    updates.setdefault("headers", rec.get("headers") or [])
                    updates.setdefault("col_highlight_rule",
                                       rec.get("col_highlight_rule") or [])
                    if rec.get("caption"):
                        updates["caption"] = rec["caption"]
                try:
                    new_child = child.model_copy(update=updates)
                    new_children.append(new_child)
                    changed = True
                except Exception:
                    for k, v in updates.items():
                        setattr(child, k, v)
                    new_children.append(child)
            else:
                new_children.append(child)
        if changed:
            slide.children = new_children


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
            kind = getattr(child, "kind", None)
            if kind not in ("image", "table"):
                new_children.append(child)
                continue
            needs_src = not getattr(child, "src_path", None)
            needs_rows = (kind == "table"
                          and not (getattr(child, "rows", None)
                                   or getattr(child, "headers", None)))
            if not needs_src and not needs_rows:
                new_children.append(child)
                continue  # already has src_path (+ rows for tables)
            rec = rendered.get(getattr(child, "layer_id", None))
            if rec and rec.get("src_path"):
                updates: dict[str, Any] = {}
                if needs_src:
                    updates["src_path"] = rec["src_path"]
                    updates["aspect_ratio"] = (rec.get("aspect_ratio")
                                               or child.aspect_ratio)
                if kind == "table":
                    updates.setdefault("rows", rec.get("rows") or [])
                    updates.setdefault("headers", rec.get("headers") or [])
                    updates.setdefault("col_highlight_rule",
                                       rec.get("col_highlight_rule") or [])
                    if rec.get("caption"):
                        updates["caption"] = rec["caption"]
                try:
                    new_child = child.model_copy(update=updates)
                    new_children.append(new_child)
                    changed = True
                except Exception:
                    for k, v in updates.items():
                        setattr(child, k, v)
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
               out_path: Path, manifest: list[dict[str, Any]],
               ctx: ToolContext) -> None:
    psd = PSDImage.new(mode="RGB", size=(cw, ch), depth=8)

    text_group = None

    for L in layers:
        bbox = L["bbox"]
        bx, by, bw, bh = bbox["x"], bbox["y"], bbox["w"], bbox["h"]

        if L["kind"] == "background":
            png = Image.open(L["src_path"])
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
        elif L["kind"] == "table":
            # v1.2.1: rebake the table PNG at the planner's bbox dims.
            # PSD has no live-table primitive, so we flatten to a pixel
            # layer — but by calling render_table_png with width_px=bw
            # and max_height_px=bh, the font autoscale produces a
            # legible PNG at bbox scale rather than a stretched one.
            try:
                tmp = ctx.layers_dir / f"table_at_bbox_{L['layer_id']}_psd.png"
                render_table_png(
                    rows=L.get("rows") or [],
                    headers=L.get("headers") or [],
                    out_path=tmp,
                    width_px=bw,
                    max_height_px=bh,
                    col_highlight_rule=L.get("col_highlight_rule") or [],
                    font_path=ctx.settings.fonts_dir / "NotoSansSC-Bold.otf",
                    bold_font_path=ctx.settings.fonts_dir / "NotoSansSC-Bold.otf",
                )
                png = Image.open(tmp).convert("RGBA")
            except Exception:
                png = Image.open(L["src_path"]).convert("RGBA")
            psd.create_pixel_layer(
                png, name=L["name"], top=by, left=bx,
                opacity=255, blend_mode=BlendMode.NORMAL,
                compression=Compression.RLE,
            )
            manifest.append({
                "layer_id": L["layer_id"], "name": L["name"], "kind": "table",
                "png_path": L["src_path"], "bbox": {"x": bx, "y": by, "w": bw, "h": bh},
            })
        elif L["kind"] == "image":
            # v1.2.1: contain-fit instead of stretch. The PSD pixel
            # layer is sized to the fitted image (letterbox inside the
            # planner's bbox); `top`/`left` shifted by the centering
            # offset so the figure doesn't drift off-bbox.
            png = Image.open(L["src_path"])
            if png.mode != "RGBA":
                png = png.convert("RGBA")
            _maybe_warn_aspect(L, png.size, (bx, by, bw, bh))
            nw, nh, off_x, off_y = _aspect_fit_contain(png.size, (bw, bh))
            if (nw, nh) != png.size:
                png = png.resize((nw, nh), Image.LANCZOS)
            psd.create_pixel_layer(
                png, name=L["name"], top=by + off_y, left=bx + off_x,
                opacity=255, blend_mode=BlendMode.NORMAL,
                compression=Compression.RLE,
            )
            manifest.append({
                "layer_id": L["layer_id"], "name": L["name"], "kind": "image",
                "png_path": L["src_path"], "bbox": {"x": bx, "y": by, "w": bw, "h": bh},
            })
        else:
            # text layer — render_text_layer produces a full-canvas transparent
            # RGBA with glyphs inside bbox, so we crop by bbox then place.
            png = Image.open(L["src_path"])
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
    image_layers = [L for L in layers if L["kind"] == "image"]
    table_layers = [L for L in layers if L["kind"] == "table"]

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

    # v1.1 paper2any: emit ingested/passthrough images as <image> elements
    # positioned by bbox, ordered by z_index so they layer correctly with text.
    # v1.2.1: preserveAspectRatio="xMidYMid meet" = SVG's letterbox — the
    # renderer scales the image into the bbox without stretching, centered.
    for L in sorted(image_layers, key=lambda x: int(x.get("z_index", 0))):
        bbox = L["bbox"]
        bx, by, bw, bh = bbox["x"], bbox["y"], bbox["w"], bbox["h"]
        with open(L["src_path"], "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        dwg.add(dwg.image(
            href=f"data:image/png;base64,{b64}",
            insert=(bx, by), size=(bw, bh),
            preserveAspectRatio="xMidYMid meet",
        ))

    # v1.2.1: table layers. Re-render at the planner's bbox so the
    # SVG-embedded PNG is font-autoscaled rather than post-squished.
    # preserveAspectRatio is still set so viewers (Illustrator / Inkscape /
    # browsers) letterbox if the embedded PNG doesn't exactly fill bbox.
    for L in sorted(table_layers, key=lambda x: int(x.get("z_index", 0))):
        bbox = L["bbox"]
        bx, by, bw, bh = bbox["x"], bbox["y"], bbox["w"], bbox["h"]
        src_path = L["src_path"]
        try:
            tmp = ctx.layers_dir / f"table_at_bbox_{L['layer_id']}_svg.png"
            render_table_png(
                rows=L.get("rows") or [],
                headers=L.get("headers") or [],
                out_path=tmp,
                width_px=bw,
                max_height_px=bh,
                col_highlight_rule=L.get("col_highlight_rule") or [],
                font_path=ctx.settings.fonts_dir / "NotoSansSC-Bold.otf",
                bold_font_path=ctx.settings.fonts_dir / "NotoSansSC-Bold.otf",
            )
            src_path = str(tmp)
        except Exception:
            pass
        with open(src_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        dwg.add(dwg.image(
            href=f"data:image/png;base64,{b64}",
            insert=(bx, by), size=(bw, bh),
            preserveAspectRatio="xMidYMid meet",
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


def _write_preview(layers: list[dict[str, Any]], cw: int, ch: int,
                   out_path: Path, ctx: ToolContext) -> None:
    base = Image.new("RGBA", (cw, ch), (255, 255, 255, 255))
    for L in layers:
        kind = L["kind"]
        if kind == "background":
            png = Image.open(L["src_path"])
            if png.mode != "RGBA":
                png = png.convert("RGBA")
            if png.size != (cw, ch):
                png = png.resize((cw, ch), Image.LANCZOS)
            base = Image.alpha_composite(base, png)
        elif kind == "table":
            # v1.2.1: re-render tables at the planner's exact bbox dims.
            # render_table_png autoscales font-size + drops rows that
            # won't fit — so an under-sized bbox degrades gracefully
            # instead of LANCZOS-squishing 14 rows into 12 px tall each.
            bbox = L["bbox"]
            bx, by, bw, bh = bbox["x"], bbox["y"], bbox["w"], bbox["h"]
            try:
                tmp = ctx.layers_dir / f"table_at_bbox_{L['layer_id']}.png"
                render_table_png(
                    rows=L.get("rows") or [],
                    headers=L.get("headers") or [],
                    out_path=tmp,
                    width_px=bw,
                    max_height_px=bh,
                    col_highlight_rule=L.get("col_highlight_rule") or [],
                    font_path=ctx.settings.fonts_dir / "NotoSansSC-Bold.otf",
                    bold_font_path=ctx.settings.fonts_dir / "NotoSansSC-Bold.otf",
                )
                png = Image.open(tmp).convert("RGBA")
            except Exception:
                # Fall back to the pre-baked src_path render if rerender fails.
                png = Image.open(L["src_path"]).convert("RGBA")
            full = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
            # render_table_png may return shorter than bh (rows truncated)
            # or narrower than bw; paste at bbox origin, let it be.
            full.paste(png, (bx, by))
            base = Image.alpha_composite(base, full)
        elif kind == "image":
            # v1.2.1: contain-fit (letterbox) instead of stretching to
            # bbox. Matches HTML's object-fit:contain behavior. A wildly
            # under-sized bbox now leaves whitespace around the figure
            # instead of distorting it.
            png = Image.open(L["src_path"])
            if png.mode != "RGBA":
                png = png.convert("RGBA")
            bbox = L["bbox"]
            bx, by, bw, bh = bbox["x"], bbox["y"], bbox["w"], bbox["h"]
            _maybe_warn_aspect(L, png.size, (bx, by, bw, bh))
            nw, nh, off_x, off_y = _aspect_fit_contain(png.size, (bw, bh))
            if (nw, nh) != png.size:
                png = png.resize((nw, nh), Image.LANCZOS)
            full = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
            full.paste(png, (bx + off_x, by + off_y))
            base = Image.alpha_composite(base, full)
        else:
            # text layer: already full-canvas transparent RGBA with glyphs
            # positioned inside bbox.
            png = Image.open(L["src_path"])
            if png.mode != "RGBA":
                png = png.convert("RGBA")
            if png.size != (cw, ch):
                full = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
                full.paste(png, (0, 0))
                png = full
            base = Image.alpha_composite(base, png)
    base.convert("RGB").save(out_path, format="PNG", optimize=True)
