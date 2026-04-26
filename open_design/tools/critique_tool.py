"""critique — thin wrapper that spawns a forked CriticAgent sub-agent.

v2.7.3 (2026-04-26): the inline `Critic` class is gone. The planner-facing
tool signature is unchanged — it still returns one CritiqueReport JSON in
`tool_result.payload` — but the work happens in
`open_design/agents/critic_agent.py` which owns its own LLMBackend, its
own turn budget, and its own trajectory file.

Why split: the inline path shared the planner's max_planner_turns budget,
saw text-only structures for deck/landing (no vision), and capped at 2
calls. The forked sub-agent gets `critic_max_turns` to its own loop, sees
slide PNGs for ALL artifact types, and emits a structured CritiqueReport
that the planner can parse to drive the next action (pass → finalize,
revise → propose_design_spec, fail → terminal).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ._contract import ToolContext, obs_error, obs_ok
from ..schema import ArtifactType, ToolResultRecord
from ..util.io import atomic_write_json
from ..util.logging import log


def critique(args: dict[str, Any], *, ctx: ToolContext) -> ToolResultRecord:
    spec = ctx.state.get("design_spec")
    if spec is None:
        return obs_error("propose_design_spec must be called first",
                         category="validation")

    prior = len(ctx.state["critique_results"])
    if prior >= ctx.settings.max_critique_iters:
        return obs_error(
            f"max_critique_iters ({ctx.settings.max_critique_iters}) reached",
            category="validation",
            payload={"max_iters": ctx.settings.max_critique_iters,
                     "prior": prior},
        )

    composition = ctx.state.get("composition")
    if composition is None:
        return obs_error(
            "no composition available — call composite first",
            category="not_found",
        )
    preview_path = args.get("preview_path") or composition.preview_path
    if not preview_path or not Path(preview_path).exists():
        return obs_error(
            "no preview.png available — call composite first",
            category="not_found",
        )

    slide_renders = _collect_slide_renders(spec, composition, Path(preview_path))
    paper_raw_text = _join_paper_raw_text(ctx)

    iteration = prior + 1
    trajectory_path = ctx.run_dir / "trajectory" / "critic.jsonl"

    from ..agents import CriticAgent
    agent = CriticAgent(ctx.settings, _resolve_artifact_type(spec))
    try:
        report = agent.critique(
            spec=spec,
            layer_manifest=composition.layer_manifest or [],
            slide_renders=slide_renders,
            paper_raw_text=paper_raw_text,
            iteration=iteration,
            trajectory_path=trajectory_path,
        )
    except Exception as e:
        return obs_error(f"critic sub-agent failed: {e}", category="api")

    ctx.state["critique_results"].append(report)

    artifact_path = ctx.run_dir / f"critique_{iteration}.json"
    atomic_write_json(artifact_path, report.model_dump(mode="json"))
    log("critique.done", iter=iteration, verdict=report.verdict,
        score=report.score, n_issues=len(report.issues))

    return obs_ok(report.model_dump(mode="json"))


def _resolve_artifact_type(spec: Any) -> ArtifactType:
    """Return the spec's artifact_type, accepting either an enum instance
    or a string (some test fixtures construct DesignSpec from raw dicts)."""
    at = getattr(spec, "artifact_type", None) or ArtifactType.POSTER
    if isinstance(at, ArtifactType):
        return at
    try:
        return ArtifactType(str(at))
    except ValueError:
        return ArtifactType.POSTER


def _collect_slide_renders(spec: Any, composition: Any,
                           preview_path: Path) -> list[Path]:
    """For deck artifacts, gather the per-slide PNGs that composite wrote
    under `composites/iter_NN/slides/`. For poster / landing the only
    render is the flattened preview.png itself."""
    artifact_type = _resolve_artifact_type(spec)
    if artifact_type != ArtifactType.DECK:
        return [preview_path]
    slides_dir = preview_path.parent / "slides"
    if not slides_dir.exists():
        return [preview_path]
    pngs = sorted(slides_dir.glob("slide_*.png"))
    return list(pngs) if pngs else [preview_path]


def _join_paper_raw_text(ctx: ToolContext) -> str | None:
    """Concatenate raw_text across all ingested documents so the critic
    can substring-search it in one place. Returns None when no ingest
    happened (free-text brief)."""
    ingested = ctx.state.get("ingested") or []
    chunks: list[str] = []
    for entry in ingested:
        text = entry.get("raw_text") if isinstance(entry, dict) else None
        if text:
            chunks.append(text)
    if not chunks:
        return None
    return "\n\n".join(chunks)
