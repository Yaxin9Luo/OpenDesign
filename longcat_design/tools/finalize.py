"""finalize — flips the runner's exit flag.

Trajectory serialization happens in the runner (it owns agent_trace and
critique_loop accumulation), not here. This tool's job is only to signal
"planner is done" so the runner can stop the tool-use loop and write the JSON.
"""

from __future__ import annotations

from typing import Any

from ._contract import ToolContext, obs_ok
from ..schema import ToolObservation


def finalize(args: dict[str, Any], *, ctx: ToolContext) -> ToolObservation:
    notes = args.get("notes", "")
    ctx.state["finalized"] = True
    ctx.state["finalize_notes"] = notes
    return obs_ok(
        "finalize signal received — runner will serialize Trajectory and exit",
        next_actions=[],
    )
