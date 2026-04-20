"""PipelineRunner — wires planner + tools + critic into one cohesive run.

Owns: per-run paths, ToolContext, trajectory serialization. Does NOT own
business logic; that lives in planner.py / critic.py / tools/*.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Settings
from .planner import PlannerLoop
from .schema import (
    ArtifactType, CompositionArtifacts, DesignSpec, LayerNode, SafeZone,
    TextEffect, Trajectory,
)
from .tools import ToolContext
from .util.io import atomic_write_json, ensure_dirs
from .util.ids import new_run_id
from .util.logging import log


class PipelineRunner:

    def __init__(self, settings: Settings):
        self.settings = settings

    def run(self, brief: str) -> tuple[Trajectory, Path]:
        run_id = new_run_id()
        run_dir = self.settings.out_dir / "runs" / run_id
        layers_dir = run_dir / "layers"
        traj_dir = self.settings.out_dir / "trajectories"
        ensure_dirs(run_dir, layers_dir, traj_dir)

        log("run.start", run_id=run_id, brief_chars=len(brief))
        wall_start = time.monotonic()

        ctx = ToolContext(
            settings=self.settings, run_dir=run_dir,
            layers_dir=layers_dir, run_id=run_id,
        )

        system_prompt = (self.settings.prompts_dir / "planner.md").read_text(encoding="utf-8")
        planner = PlannerLoop(self.settings, system_prompt)
        trace = planner.run(brief, ctx)

        spec = ctx.state.get("design_spec")
        composition = ctx.state.get("composition")
        if spec is None:
            raise RuntimeError("planner exited without proposing a DesignSpec")
        if composition is None:
            raise RuntimeError("planner exited without producing composition artifacts")

        # Landing + Deck: the authoritative layer tree is the nested tree on
        # the DesignSpec (rendered_layers may be empty or hold only raw image
        # records, not a materialised graph). Poster: materialize from the
        # rendered_layers blackboard, which is built up by render_text_layer
        # and generate_background.
        if spec.artifact_type in (ArtifactType.LANDING, ArtifactType.DECK):
            final_layer_graph = list(spec.layer_graph or [])
        else:
            final_layer_graph = _materialize_layer_graph(ctx.state["rendered_layers"])

        wall_s = round(time.monotonic() - wall_start, 2)
        in_tok, out_tok = planner.token_totals
        cost = _estimate_cost(in_tok, out_tok, n_critiques=len(ctx.state["critique_results"]))

        traj = Trajectory(
            run_id=run_id,
            created_at=datetime.now(),
            brief=brief,
            design_spec=spec,
            layer_graph=final_layer_graph,
            agent_trace=trace,
            critique_loop=ctx.state["critique_results"],
            composition=composition,
            metadata={
                "planner_model": self.settings.planner_model,
                "critic_model": self.settings.critic_model,
                "image_model": self.settings.image_model,
                "total_input_tokens": in_tok,
                "total_output_tokens": out_tok,
                "estimated_cost_usd": cost,
                "wall_time_s": wall_s,
                "max_critique_iters": self.settings.max_critique_iters,
                "max_planner_turns": self.settings.max_planner_turns,
                "finalize_notes": ctx.state.get("finalize_notes", ""),
                "version": "v0",
            },
        )

        traj_path = traj_dir / f"{run_id}.json"
        atomic_write_json(traj_path, traj.model_dump(mode="json"))
        log("run.done", run_id=run_id, traj=str(traj_path),
            wall_s=wall_s, cost_usd=cost,
            n_layers=len(final_layer_graph), n_critiques=len(ctx.state["critique_results"]))

        return traj, traj_path


def _materialize_layer_graph(rendered: dict[str, dict[str, Any]]) -> list[LayerNode]:
    """Convert ctx.state['rendered_layers'] (dict-by-id) to a flat LayerNode list ordered by z."""
    nodes: list[LayerNode] = []
    for L in sorted(rendered.values(), key=lambda x: int(x.get("z_index", 0))):
        bbox = L["bbox"]
        bb = SafeZone(x=int(bbox["x"]), y=int(bbox["y"]),
                      w=int(bbox["w"]), h=int(bbox["h"]))
        eff_dict = L.get("effects") or {}
        effects = TextEffect(**eff_dict) if (eff_dict and L.get("kind") == "text") else None
        nodes.append(LayerNode(
            layer_id=L["layer_id"],
            name=L["name"],
            kind=L["kind"],
            z_index=int(L.get("z_index", 0)),
            bbox=bb,
            text=L.get("text"),
            font_family=L.get("font_family"),
            font_size_px=L.get("font_size_px"),
            align=L.get("align"),
            effects=effects,
            prompt=L.get("prompt"),
            aspect_ratio=L.get("aspect_ratio"),
            image_size=L.get("image_size"),
            src_path=L.get("src_path"),
        ))
    return nodes


def _estimate_cost(input_tokens: int, output_tokens: int, *, n_critiques: int) -> float:
    """Rough estimate; tighten after measuring real runs."""
    opus_in_per_mtok = 15.0
    opus_out_per_mtok = 75.0
    nbp_per_image_2k = 0.15
    planner_cost = (input_tokens / 1e6) * opus_in_per_mtok + (output_tokens / 1e6) * opus_out_per_mtok
    return round(planner_cost + nbp_per_image_2k + 0.05 * max(n_critiques, 0), 4)
