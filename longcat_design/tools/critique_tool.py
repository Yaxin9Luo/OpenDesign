"""critique — vision call on the latest preview, returns a CritiqueResult.

Wraps `longcat_design.critic.Critic` so the planner can invoke critiques inline.
The full CritiqueResult is appended to ctx.state['critique_results'] and also
serialized to disk (artifact path returned for SFT/DPO replay).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ._contract import ToolContext, obs_error, obs_ok
from ..schema import ToolObservation
from ..util.io import atomic_write_json
from ..util.logging import log


def critique(args: dict[str, Any], *, ctx: ToolContext) -> ToolObservation:
    spec = ctx.state.get("design_spec")
    if spec is None:
        return obs_error("propose_design_spec must be called first")

    prior = len(ctx.state["critique_results"])
    if prior >= ctx.settings.max_critique_iters:
        return obs_error(
            f"max_critique_iters ({ctx.settings.max_critique_iters}) reached — "
            "stop revising and call finalize",
            next_actions=["call finalize"],
        )

    composition = ctx.state.get("composition")
    preview_path = args.get("preview_path") or (
        composition.preview_path if composition else None
    )
    if not preview_path or not Path(preview_path).exists():
        return obs_error("no preview.png available — call composite first")

    from ..critic import Critic
    critic = Critic(ctx.settings)
    iteration = prior + 1
    try:
        result, thinking_records = critic.evaluate(
            preview_path=Path(preview_path),
            design_spec=spec,
            layer_manifest=composition.layer_manifest if composition else [],
            iteration=iteration,
            max_iters=ctx.settings.max_critique_iters,
        )
    except Exception as e:
        return obs_error(f"critic call failed: {e}")

    ctx.state["critique_results"].append(result)
    # Stash extended-thinking blocks (if any) so PlannerLoop can append them
    # as actor="critic" type="reasoning" steps in the agent_trace. Uses a
    # single-use slot — planner pops it after the `critique` tool result.
    if thinking_records:
        ctx.state["_pending_critic_thinking"] = thinking_records
    artifact_path = ctx.run_dir / f"critique_{iteration}.json"
    atomic_write_json(artifact_path, result.model_dump(mode="json"))
    log("critique.done", iter=iteration, verdict=result.verdict, score=result.score,
        n_issues=len(result.issues))

    next_actions = (
        ["call finalize — design passed self-review"] if result.verdict == "pass"
        else ["adjust text layers (positions/colors/sizes), call composite again, "
              "then optionally critique once more"] if result.verdict == "revise"
        else ["call finalize — critique flagged issues but max iters reached"]
    )
    return obs_ok(
        f"Critique iter={iteration} verdict={result.verdict} score={result.score:.2f} "
        f"({len(result.issues)} issues)",
        artifacts=[str(artifact_path)],
        next_actions=next_actions,
    )
