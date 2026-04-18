"""switch_artifact_type — declare what kind of artifact we're producing.

Sets `ctx.state["artifact_type"]` to one of `poster | deck | landing`. This:
- Drives renderer selection in `composite` (different output formats per type).
- Gets used as a fallback default by `propose_design_spec` when the spec JSON
  omits `artifact_type`.
- Creates a clean trace step (type=`artifact_switch`) in the trajectory so
  SFT / analysis can replay "when did the planner decide to make a landing
  page vs a poster" without parsing free-form text.

Call this tool:
- At the START of a new artifact (first session turn, OR mid-session when the
  user asks for a different artifact type than the current one).
- BEFORE `propose_design_spec` — the spec should carry the type as a field,
  but this tool records the intent as its own event.

Idempotent: calling twice with the same type is a no-op (beyond the trace step).
Re-calling with a different type triggers a new artifact; prior `rendered_layers`
and `composition` are preserved in state but are considered "previous artifact"
(a future runner can archive them under a sub-dir per artifact — v1.1+ work).
"""

from __future__ import annotations

from typing import Any

from ._contract import ToolContext, obs_error, obs_ok
from ..schema import ArtifactType, ToolObservation
from ..util.logging import log


VALID_TYPES = {t.value for t in ArtifactType}


def switch_artifact_type(args: dict[str, Any], *, ctx: ToolContext) -> ToolObservation:
    raw_type = args.get("type")
    if raw_type is None:
        return obs_error(
            "missing required arg 'type'",
            next_actions=[f"call switch_artifact_type with type in {sorted(VALID_TYPES)}"],
        )
    if raw_type not in VALID_TYPES:
        return obs_error(
            f"invalid artifact type {raw_type!r}; allowed: {sorted(VALID_TYPES)}",
            next_actions=["retry with one of the allowed values"],
        )

    previous = ctx.state.get("artifact_type", "poster")
    ctx.state["artifact_type"] = raw_type
    is_switch = previous != raw_type

    log(
        "artifact.switch" if is_switch else "artifact.reaffirm",
        previous=previous,
        current=raw_type,
    )

    if is_switch:
        summary = f"artifact_type: {previous} → {raw_type}"
        next_actions = _guidance_for(raw_type, switching=True)
    else:
        summary = f"artifact_type confirmed: {raw_type} (no change)"
        next_actions = _guidance_for(raw_type, switching=False)

    return obs_ok(summary, next_actions=next_actions)


def _guidance_for(artifact_type: str, *, switching: bool) -> list[str]:
    """Hint the planner about what to do next, per artifact type."""
    base = [
        "call propose_design_spec with a DesignSpec matching this artifact type",
    ]
    type_hints: dict[str, list[str]] = {
        "poster": [
            "poster = absolutely-positioned layers over a text-free background",
            "canvas e.g. 1536x2048 (3:4) or 2048x1536 (4:3); background via "
            "generate_background, text via render_text_layer",
        ],
        "deck": [
            "deck = N slides; one LayerNode per slide, kind may grow to 'slide' in v1.1",
            "output format will be PPTX (python-pptx native type frames) once "
            "the deck renderer lands — for now the spec + text layers compose "
            "via the existing poster path per-slide",
        ],
        "landing": [
            "landing = single self-contained HTML page with semantic sections "
            "(header / hero / features / cta / footer)",
            "output is a single .html with inline Tailwind + base64 assets + "
            "WOFF2-subset fonts — use flow layout, not absolute positioning",
        ],
    }
    extras = type_hints.get(artifact_type, [])
    if switching:
        base.append(
            "prior rendered_layers and composition are retained in state but "
            "represent the previous artifact; the new spec starts a new one"
        )
    return base + extras
