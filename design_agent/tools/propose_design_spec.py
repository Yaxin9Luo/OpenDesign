"""propose_design_spec — validate the planner's initial DesignSpec.

Stores the spec in ctx.state['design_spec']; subsequent tools look it up there.
Re-calling this tool replaces the spec (planner may revise mid-run).
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from ._contract import ToolContext, obs_error, obs_ok
from ..schema import DesignSpec, ToolObservation
from ..util.logging import log


def propose_design_spec(args: dict[str, Any], *, ctx: ToolContext) -> ToolObservation:
    raw = args.get("design_spec")
    if raw is None:
        return obs_error("missing 'design_spec' in args")
    try:
        spec = DesignSpec.model_validate(raw)
    except ValidationError as e:
        return obs_error(
            f"DesignSpec validation failed: {e.errors(include_url=False)}",
            next_actions=["fix the JSON shape and re-call propose_design_spec"],
        )

    is_revision = ctx.state.get("design_spec") is not None
    ctx.state["design_spec"] = spec
    log("spec.proposed", revision=is_revision,
        canvas=spec.canvas, n_layers=len(spec.layer_graph))

    next_actions = (
        ["re-render only the changed layers, then call composite again"]
        if is_revision
        else ["call generate_background with safe_zones derived from layer_graph",
              "then render_text_layer for each text element"]
    )
    return obs_ok(
        f"DesignSpec accepted ({spec.canvas['w_px']}×{spec.canvas['h_px']}, "
        f"{len(spec.layer_graph)} planned layers)"
        + (" — REVISION" if is_revision else ""),
        next_actions=next_actions,
    )
