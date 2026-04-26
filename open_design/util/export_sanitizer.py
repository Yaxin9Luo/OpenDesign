"""Drop placeholder text, debug-named shapes, and empty children from final spec.

Runs as a post-render sanitizer in `_composite_deck` BEFORE `write_pptx()`.
Operates on structural properties of the spec — no per-paper or per-source-type
heuristics. See ~/.claude/plans/export-block-eager-wilkinson.md for context.

Targets two visual-trust bugs surfaced by the 2026-04-26 longcat-next dogfood:

- B1: placeholder text leaks ("Paper Title Goes Here", "arxiv.org/abs/XXXX",
  debug-named callouts like ``callout_05_a``, "Annotation 12") that the
  planner emitted as scaffolding while drafting and never rewrote.
- B4: empty callout shapes — a valid ``anchor_layer_id`` (so the orphan
  detector keeps them) but ``callout_text=""`` produces a stray white
  rectangle on the rendered slide.

Schema notes (so the matchers stay aligned with ``open_design/schema.py``):
- Every LayerNode has ``layer_id`` (not ``id``), ``name``, and ``kind``.
- Text content for ``kind="text"`` lives in ``text``; for ``kind="callout"``
  the visible label is ``callout_text`` (``text`` is unused on callouts).
- ``LayerKind`` is one of: background, text, brand_asset, group, section,
  image, slide, table, cta, callout. We only drop *text-bearing* kinds —
  ``text``, ``callout`` — so background / image / table / slide are never
  removed even when a leaf has no ``text``.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - import-cycle guard
    from open_design.schema import DesignSpec, LayerNode

# Placeholder substrings (lowercase) that mark scaffolding the planner
# forgot to overwrite. Match is `needle in text.lower()` so partial
# strings ("paper title goes here" inside a longer sentence) still trigger.
PLACEHOLDER_SUBSTRINGS: tuple[str, ...] = (
    "paper title goes here",
    "author one",
    "author two",
    "affiliation goes here",
    "arxiv.org/abs/xxxx",
    "example@example.com",
    "yyyy-mm-dd",
    "lorem ipsum",
    "your name here",
    *(f"annotation {i}" for i in range(1, 21)),
)

# Debug-named shapes: planner-internal scaffolding ids like
# ``callout_05_a``, ``annotation_12``, ``debug-3``, ``placeholder_01``.
# Pattern: <prefix>[_-]?<digits>([_-]<single letter>)?
DEBUG_NAME_RE = re.compile(
    r"^(callout|annotation|debug|scratch|placeholder|test|tmp|todo)"
    r"[_-]?\d+([_-][a-z])?$",
    re.IGNORECASE,
)


def _node_visible_text(node: "LayerNode") -> str:
    """Return whichever text field the renderer would actually display.

    For ``kind="callout"`` the visible label is ``callout_text``; for every
    other text-bearing kind it's ``text``. Returns ``""`` (not None) so the
    caller can stripe-and-test in a single expression.
    """
    kind = getattr(node, "kind", None)
    if kind == "callout":
        return (getattr(node, "callout_text", "") or "").strip()
    return (getattr(node, "text", "") or "").strip()


def _is_placeholder_text(text: str) -> bool:
    """True when ``text`` contains any known placeholder needle."""
    if not text:
        return False
    lower = text.strip().lower()
    return any(needle in lower for needle in PLACEHOLDER_SUBSTRINGS)


def _is_debug_named_empty(node: "LayerNode") -> bool:
    """True when the node's ``name`` matches ``DEBUG_NAME_RE`` AND it
    cannot prove it carries real content. The empty-text condition
    prevents dropping a real callout that happens to keep its scaffold
    name (``callout_05_a``) but does carry anchor copy.

    Visual-only callouts (``callout_style in {"highlight", "circle"}``)
    are exempt — they intentionally have no text, so the name alone is
    not enough evidence to drop them. Same exemption for non-text
    kinds that carry their content elsewhere (image src_path, table
    rows/headers).
    """
    name = (getattr(node, "name", "") or "").strip()
    if not DEBUG_NAME_RE.match(name):
        return False
    kind = getattr(node, "kind", None)
    if kind == "callout" and getattr(node, "callout_style", None) in ("highlight", "circle"):
        return False
    if getattr(node, "src_path", None):
        return False
    if getattr(node, "rows", None) or getattr(node, "headers", None):
        return False
    return not _node_visible_text(node)


def _is_empty_leaf(node: "LayerNode") -> bool:
    """True when a text-bearing leaf carries no text and no other content.

    Scope:
    - ``kind="text"``: drop when ``text`` is empty and there are no
      children / src_path.
    - ``kind="callout"`` with ``callout_style="label"``: drop when
      ``callout_text`` is empty (B4 — empty white rectangle defect).
      The other two callout styles — ``highlight`` (rectangle outline)
      and ``circle`` (ellipse outline) — are visual-only by design and
      MUST be kept even when ``callout_text`` is empty (see schema
      docstring at LayerNode.callout_style).

    Backgrounds, images, tables, sections, slides, and ctas are never
    dropped here.
    """
    kind = getattr(node, "kind", None)
    if kind == "callout":
        # Only label-style callouts carry visible text; highlight/circle
        # are pure-shape annotations and stay even without text.
        if getattr(node, "callout_style", None) != "label":
            return False
    elif kind != "text":
        return False
    if _node_visible_text(node):
        return False
    if getattr(node, "children", None):
        return False
    if getattr(node, "src_path", None):
        return False
    # tables carry rows/headers — keep them even with empty text
    if getattr(node, "rows", None) or getattr(node, "headers", None):
        return False
    return True


def _filter_children(
    node: "LayerNode",
    slide_id: str,
    warnings: list[dict[str, Any]],
) -> "LayerNode":
    """Recursively walk ``node``, returning a copy whose descendants have
    been filtered. The node itself is never dropped here — the caller
    decides at the parent level. Slides (top-level) are likewise never
    dropped; only their children get filtered.
    """
    children = list(getattr(node, "children", None) or [])
    if not children:
        return node

    new_children: list[Any] = []
    changed = False
    for child in children:
        # Recurse first so deeply-nested debris is caught before the
        # parent's drop check runs.
        cleaned = _filter_children(child, slide_id, warnings)
        if cleaned is not child:
            changed = True

        visible = _node_visible_text(cleaned)
        if _is_placeholder_text(visible):
            warnings.append({
                "slide_id": slide_id,
                "layer_id": getattr(cleaned, "layer_id", "") or "",
                "reason": "placeholder_text",
                "preview": visible[:80],
            })
            changed = True
            continue
        if _is_debug_named_empty(cleaned):
            warnings.append({
                "slide_id": slide_id,
                "layer_id": getattr(cleaned, "layer_id", "") or "",
                "reason": "debug_name_empty",
                "preview": (getattr(cleaned, "name", "") or "")[:80],
            })
            changed = True
            continue
        if _is_empty_leaf(cleaned):
            warnings.append({
                "slide_id": slide_id,
                "layer_id": getattr(cleaned, "layer_id", "") or "",
                "reason": "empty_leaf",
                "preview": (getattr(cleaned, "name", "") or "")[:80],
            })
            changed = True
            continue
        new_children.append(cleaned)

    if not changed:
        return node
    return node.model_copy(update={"children": new_children})


def sanitize_design_spec(
    spec: "DesignSpec",
) -> tuple["DesignSpec", list[dict[str, Any]]]:
    """Returns ``(sanitized_spec, warnings)``.

    Drops from descendants of every top-level node in ``layer_graph``:
    - children whose visible text matches ``PLACEHOLDER_SUBSTRINGS`` (B1)
    - empty-visible-text shapes whose ``name`` matches ``DEBUG_NAME_RE`` (B4)
    - text/callout leaves with no visible text and no other content (B4)

    Top-level nodes (slides on decks; sections on landings) are never
    dropped — only their descendants. Returns a new ``DesignSpec``;
    never mutates the input.
    """
    warnings: list[dict[str, Any]] = []
    layer_graph = list(getattr(spec, "layer_graph", []) or [])
    new_layer_graph: list[Any] = []
    changed = False
    for node in layer_graph:
        slide_id = (
            getattr(node, "layer_id", "")
            or getattr(node, "name", "")
            or "<unknown>"
        )
        cleaned = _filter_children(node, slide_id, warnings)
        if cleaned is not node:
            changed = True
        new_layer_graph.append(cleaned)
    if not changed:
        return spec, warnings
    return spec.model_copy(update={"layer_graph": new_layer_graph}), warnings
