"""edit_layer — apply a targeted subset-diff to a previously rendered text layer.

Semantics:
  - Reads current layer state from ctx.state["rendered_layers"][layer_id].
  - Merges the `diff` onto it (nested merge for bbox + effects; replace otherwise).
  - Delegates to render_text_layer which overwrites both the PNG on disk and
    the ctx.state entry. No side effects on other layers, no implicit composite.

Scope (v1.0 #5): text layers only. Background edits go through
`generate_background`; brand-asset edits go through `fetch_brand_asset`.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..schema import ToolResultRecord
from ._contract import ToolContext, obs_error, obs_ok
from .render_text_layer import render_text_layer


# Fields the planner may pass inside `diff`. Anything else is rejected so we
# don't silently accept a misspelled field (e.g. `color` instead of `fill`).
_ALLOWED_DIFF_FIELDS: frozenset[str] = frozenset({
    "text", "font_family", "font_size_px", "fill",
    "bbox", "align", "z_index", "effects",
})


def edit_layer(args: dict[str, Any], *, ctx: ToolContext) -> ToolResultRecord:
    layer_id = args.get("layer_id")
    diff = args.get("diff") or {}

    if not layer_id:
        return obs_error("edit_layer: 'layer_id' is required", category="validation")
    if not isinstance(diff, dict) or not diff:
        return obs_error(
            "edit_layer: 'diff' must be a non-empty object "
            "(subset of editable fields to merge onto current layer state)",
            category="validation",
        )

    unknown = sorted(set(diff) - _ALLOWED_DIFF_FIELDS)
    if unknown:
        return obs_error(
            f"edit_layer: unknown diff field(s) {unknown}. "
            f"Allowed: {sorted(_ALLOWED_DIFF_FIELDS)}",
            category="validation",
        )

    rendered = ctx.state.get("rendered_layers", {})
    current = rendered.get(layer_id)
    if current is None:
        return obs_error(
            f"edit_layer: layer '{layer_id}' not found. "
            f"Available layer_ids: {sorted(rendered.keys()) or '[]'}.",
            category="not_found",
            payload={"available_layer_ids": sorted(rendered.keys())},
        )

    if current.get("kind") != "text":
        return obs_error(
            f"edit_layer: layer '{layer_id}' has kind='{current.get('kind')}', "
            "but edit_layer only supports kind='text'.",
            category="validation",
            payload={"layer_id": layer_id, "kind": current.get("kind")},
        )

    merged = deepcopy(current)
    for k, v in diff.items():
        if k == "bbox" and isinstance(v, dict):
            merged["bbox"] = {**(merged.get("bbox") or {}), **v}
        elif k == "effects" and isinstance(v, dict):
            merged["effects"] = {**(merged.get("effects") or {}), **v}
        else:
            merged[k] = v

    render_args = {
        "layer_id": layer_id,
        "name": merged.get("name") or layer_id,
        "text": merged.get("text", ""),
        "font_family": merged.get("font_family"),
        "font_size_px": int(merged.get("font_size_px", 0)),
        "fill": merged.get("fill", "#000000"),
        "bbox": merged["bbox"],
        "align": merged.get("align", "left"),
        "z_index": int(merged.get("z_index", 1)),
        "effects": merged.get("effects") or {},
    }

    result = render_text_layer(render_args, ctx=ctx)
    if result.status != "ok":
        return result

    # Augment payload with the diff fields the policy requested. render_text_layer
    # already returned sha256 + layer_id; we add `fields_changed` so the policy
    # has a record of what its edit actually touched.
    payload = dict(result.payload)
    payload["fields_changed"] = sorted(diff.keys())
    return obs_ok(payload)
