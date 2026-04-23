"""propose_design_spec — validate the planner's initial DesignSpec.

Stores the spec in ctx.state['design_spec']; subsequent tools look it up
there. Re-calling this tool replaces the spec (planner may revise mid-run).

v2 training-data shape: payload is `{"n_layers", "artifact_type", "is_revision"}`
— the spec itself is preserved verbatim in the corresponding tool_call.tool_args
so duplicating it in the result would be pure waste. Validation errors
return the full pydantic errors() list under category="validation" so the
policy can learn structured-output recovery.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from ._contract import ToolContext, obs_error, obs_ok
from ..schema import DesignSpec, ToolResultRecord
from ..util.logging import log


def propose_design_spec(args: dict[str, Any], *, ctx: ToolContext) -> ToolResultRecord:
    raw = args.get("design_spec")
    if raw is None:
        return obs_error("missing 'design_spec' in args", category="validation")

    # Fall back to ctx.state["artifact_type"] if planner omitted it from the spec.
    if isinstance(raw, dict) and "artifact_type" not in raw:
        raw = {**raw, "artifact_type": ctx.state.get("artifact_type", "poster")}

    try:
        spec = DesignSpec.model_validate(raw)
    except ValidationError as e:
        return obs_error(
            f"DesignSpec validation failed: {e.errors(include_url=False)}",
            category="validation",
        )

    # Keep ctx.state in sync with the spec's declared type (spec wins on mismatch).
    state_type = ctx.state.get("artifact_type", "poster")
    if spec.artifact_type.value != state_type:
        log("artifact.spec_override",
            prior_state=state_type, spec_declared=spec.artifact_type.value)
        ctx.state["artifact_type"] = spec.artifact_type.value

    is_revision = ctx.state.get("design_spec") is not None
    ctx.state["design_spec"] = spec
    log("spec.proposed", revision=is_revision,
        artifact_type=spec.artifact_type.value,
        canvas=spec.canvas, n_layers=len(spec.layer_graph))

    return obs_ok({
        "artifact_type": spec.artifact_type.value,
        "n_layers": len(spec.layer_graph),
        "canvas": {"w_px": spec.canvas["w_px"], "h_px": spec.canvas["h_px"]},
        "is_revision": is_revision,
    })
