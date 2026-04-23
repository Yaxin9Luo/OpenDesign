"""critique — vision call on the latest preview, returns a CritiqueResult.

Wraps `open_design.critic.Critic` so the planner can invoke critiques
inline. The full CritiqueResult is appended to ctx.state['critique_results']
(runtime cache) AND embedded verbatim into the tool_result payload (for
training data — verdict / score / issues / rationale are the ground-truth
reward signal that mid-training SFT and RL post-training need).

The critic's extended-thinking blocks (if any) are stashed in
ctx.state["_pending_critic_thinking"] and picked up by PlannerLoop, which
emits them as actor="critic" type="reasoning" steps right after this tool
result. That's the second CoT stream in the trajectory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ._contract import ToolContext, obs_error, obs_ok
from ..schema import ToolResultRecord
from ..util.io import atomic_write_json
from ..util.logging import log


def critique(args: dict[str, Any], *, ctx: ToolContext) -> ToolResultRecord:
    spec = ctx.state.get("design_spec")
    if spec is None:
        return obs_error("propose_design_spec must be called first", category="validation")

    prior = len(ctx.state["critique_results"])
    if prior >= ctx.settings.max_critique_iters:
        return obs_error(
            f"max_critique_iters ({ctx.settings.max_critique_iters}) reached",
            category="validation",
            payload={"max_iters": ctx.settings.max_critique_iters, "prior": prior},
        )

    composition = ctx.state.get("composition")
    preview_path = args.get("preview_path") or (
        composition.preview_path if composition else None
    )
    if not preview_path or not Path(preview_path).exists():
        return obs_error(
            "no preview.png available — call composite first",
            category="not_found",
        )

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
        return obs_error(f"critic call failed: {e}", category="api")

    ctx.state["critique_results"].append(result)
    if thinking_records:
        ctx.state["_pending_critic_thinking"] = thinking_records

    # Persist the raw critique result alongside the run artifacts (for
    # offline inspection / DPO pair extraction). NOT referenced from the
    # trajectory JSON — it's a sidecar.
    artifact_path = ctx.run_dir / f"critique_{iteration}.json"
    atomic_write_json(artifact_path, result.model_dump(mode="json"))
    log("critique.done", iter=iteration, verdict=result.verdict, score=result.score,
        n_issues=len(result.issues))

    # FULL critique dumped into the tool_result payload — this is the
    # ground-truth reward signal for RL and a high-value SFT label.
    return obs_ok(result.model_dump(mode="json"))
