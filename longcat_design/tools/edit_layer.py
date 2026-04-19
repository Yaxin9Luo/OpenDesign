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

from ..schema import ToolObservation
from ._contract import ToolContext, obs_error, obs_not_found
from .render_text_layer import render_text_layer


# Fields the planner may pass inside `diff`. Anything else is rejected so we
# don't silently accept a misspelled field (e.g. `color` instead of `fill`).
_ALLOWED_DIFF_FIELDS: frozenset[str] = frozenset({
    "text", "font_family", "font_size_px", "fill",
    "bbox", "align", "z_index", "effects",
})


def edit_layer(args: dict[str, Any], *, ctx: ToolContext) -> ToolObservation:
    layer_id = args.get("layer_id")
    diff = args.get("diff") or {}

    if not layer_id:
        return obs_error("edit_layer: 'layer_id' is required")
    if not isinstance(diff, dict) or not diff:
        return obs_error(
            "edit_layer: 'diff' must be a non-empty object "
            "(subset of editable fields to merge onto current layer state)"
        )

    unknown = sorted(set(diff) - _ALLOWED_DIFF_FIELDS)
    if unknown:
        return obs_error(
            f"edit_layer: unknown diff field(s) {unknown}. "
            f"Allowed: {sorted(_ALLOWED_DIFF_FIELDS)}"
        )

    rendered = ctx.state.get("rendered_layers", {})
    current = rendered.get(layer_id)
    if current is None:
        return obs_not_found(
            f"edit_layer: layer '{layer_id}' not found. "
            f"Available layer_ids: {sorted(rendered.keys()) or '[]'}. "
            "Call render_text_layer first (or re-issue from a fresh "
            "propose_design_spec if this is a new turn)."
        )

    if current.get("kind") != "text":
        return obs_error(
            f"edit_layer: layer '{layer_id}' has kind='{current.get('kind')}', "
            "but edit_layer only supports kind='text'. "
            "For backgrounds call generate_background with the same layer_id; "
            "for brand assets call fetch_brand_asset."
        )

    merged = deepcopy(current)
    for k, v in diff.items():
        if k == "bbox" and isinstance(v, dict):
            merged["bbox"] = {**(merged.get("bbox") or {}), **v}
        elif k == "effects" and isinstance(v, dict):
            merged["effects"] = {**(merged.get("effects") or {}), **v}
        else:
            merged[k] = v

    # render_text_layer requires the canvas via ctx.state["design_spec"]; if
    # it's missing it will produce a clear error — no need to duplicate that
    # check here. We just forward the merged payload.
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

    obs = render_text_layer(render_args, ctx=ctx)
    if obs.status not in ("ok", "partial"):
        return obs

    changed = ", ".join(sorted(diff.keys()))
    wrapped = obs.model_copy(update={
        "summary": (
            f"edit_layer '{render_args['name']}' ({layer_id}): {changed} "
            f"→ re-rendered. {obs.summary}"
        ),
    })
    return wrapped
