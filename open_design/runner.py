"""PipelineRunner — wires planner + tools + critic into one cohesive run.

Owns: per-run paths, ToolContext, trajectory serialization. Does NOT own
business logic; that lives in planner.py / critic.py / tools/*.

v2 trajectory shape (training-data only): produces a DistillTrajectory
containing only model decisions + lean tool results + episode-level reward.
The product-side artifacts (HTML / PSD / SVG / PPTX) live on disk under
out/runs/<run_id>/ and are NOT referenced from the trajectory JSON.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .agents import ClaimGraphExtractor, EnhancerResult, PromptEnhancer
from .agents.claim_graph_extractor import EXTRACT_FAIL_THESIS
from .agents.prompt_enhancer import load_enhancer_system_prompt
from .config import Settings
from .planner import PlannerLoop
from .schema import (
    ArtifactType, ClaimGraph, DistillTrajectory, TrainingMetadata,
)
from .tools import ToolContext
from .util.claim_graph_validator import validate_claim_graph
from .util.io import atomic_write_json, ensure_dirs
from .util.ids import new_run_id
from .util.logging import log


class PipelineRunner:

    def __init__(self, settings: Settings):
        self.settings = settings

    def run(self, brief: str,
            attachments: list[Path] | None = None,
            template: str | None = None,
            skip_enhancer: bool = False,
            no_claim_graph: bool = False) -> tuple[DistillTrajectory, Path]:
        run_id = new_run_id()
        run_dir = self.settings.out_dir / "runs" / run_id
        layers_dir = run_dir / "layers"
        # v2.7.3 — sub-agents (CriticAgent, future ClaimGraphExtractor)
        # write per-call JSONL into this dir alongside the planner's
        # top-level trajectory JSON. Each sub-agent owns its own file so
        # SFT/DPO can flatten by actor without parsing the run-level JSON.
        sub_traj_dir = run_dir / "trajectory"
        traj_dir = self.settings.out_dir / "trajectories"
        ensure_dirs(run_dir, layers_dir, sub_traj_dir, traj_dir)

        # v1.1: inject an "Attached files" prologue into the brief so the
        # planner knows to call `ingest_document` FIRST. We don't change the
        # planner signature — attachments travel as part of the brief text.
        # v2.3: same mechanism for --template — a "Template:" block with the
        # resolved canvas lands in the prologue BEFORE attachments.
        attachments = list(attachments or [])
        effective_brief = _apply_template_prologue(brief, template)
        effective_brief = _apply_attachment_prologue(effective_brief, attachments)

        log("run.start", run_id=run_id, brief_chars=len(effective_brief),
            attachments=len(attachments), template=template or "(none)",
            skip_enhancer=skip_enhancer)
        wall_start = time.monotonic()

        # v2.4 Prompt Enhancer — runs before PlannerLoop. `--skip-enhancer`
        # bypasses unconditionally; otherwise the settings gate decides.
        enhancer_result = _run_enhancer(
            self.settings, effective_brief, skip_enhancer=skip_enhancer,
        )
        planner_input_brief = enhancer_result.enhanced_brief

        ctx = ToolContext(
            settings=self.settings, run_dir=run_dir,
            layers_dir=layers_dir, run_id=run_id,
        )

        # v2.8.0 ClaimGraph extractor — runs between enhancer and planner
        # whenever the brief attaches a PDF and the stage is enabled.
        # Result lives in `ctx.state["claim_graph"]` so the planner prompt
        # + critic can reference it; on validation failure we drop back to
        # None and degrade to v2.7.3 chapter-order behavior.
        claim_graph = _run_claim_graph_extractor(
            self.settings, attachments,
            no_claim_graph=no_claim_graph,
            sub_traj_dir=sub_traj_dir,
        )
        ctx.state["claim_graph"] = claim_graph

        system_prompt = (self.settings.prompts_dir / "planner.md").read_text(encoding="utf-8")
        planner = PlannerLoop(self.settings, system_prompt)
        trace = planner.run(planner_input_brief, ctx)

        # Both spec + composition are runtime-only state now; we still
        # require them to have been produced (sanity check that the planner
        # actually completed a full workflow) but do NOT persist them in
        # the trajectory JSON.
        spec = ctx.state.get("design_spec")
        composition = ctx.state.get("composition")
        if spec is None:
            log("run.warning", reason="planner exited without proposing a DesignSpec")
        if composition is None:
            log("run.warning", reason="planner exited without producing composition artifacts")

        wall_s = round(time.monotonic() - wall_start, 2)
        in_tok, out_tok = planner.token_totals
        cache_read, cache_create = planner.cache_totals
        cost = _estimate_cost(
            in_tok, out_tok,
            n_critiques=len(ctx.state["critique_results"]),
            enhancer_in=enhancer_result.input_tokens,
            enhancer_out=enhancer_result.output_tokens,
        )

        terminal_status, final_reward = _derive_episode_outcome(
            ctx, finalized=ctx.state.get("finalized", False),
            spec_present=spec is not None, composition_present=composition is not None,
        )

        # The trajectory's top-level `brief` is what the planner actually
        # saw (post-enhancement when the stage ran). `original_brief` in
        # metadata preserves the pre-enhancement input for A/B analysis.
        traj = DistillTrajectory(
            run_id=run_id,
            brief=planner_input_brief,
            agent_trace=trace,
            final_reward=final_reward,
            terminal_status=terminal_status,
            metadata=TrainingMetadata(
                schema_version="v2",
                planner_model=self.settings.planner_model,
                critic_model=self.settings.critic_model,
                image_model=self.settings.image_model,
                planner_thinking_budget=self.settings.planner_thinking_budget,
                critic_thinking_budget=self.settings.critic_thinking_budget,
                interleaved_thinking=self.settings.enable_interleaved_thinking,
                total_input_tokens=in_tok,
                total_output_tokens=out_tok,
                total_cache_read_tokens=cache_read,
                total_cache_creation_tokens=cache_create,
                estimated_cost_usd=cost,
                wall_time_s=wall_s,
                source="agent_run",
                enhancer_model=enhancer_result.model,
                enhancer_skipped=enhancer_result.skipped,
                enhancer_skip_reason=enhancer_result.skip_reason,
                enhancer_input_tokens=enhancer_result.input_tokens,
                enhancer_output_tokens=enhancer_result.output_tokens,
                enhancer_wall_time_s=enhancer_result.wall_time_s,
                original_brief=(effective_brief
                                if not enhancer_result.skipped else ""),
            ),
        )

        traj_path = traj_dir / f"{run_id}.json"
        atomic_write_json(traj_path, traj.model_dump(mode="json"))
        log("run.done", run_id=run_id, traj=str(traj_path),
            wall_s=wall_s, cost_usd=cost,
            terminal_status=terminal_status, final_reward=final_reward,
            n_steps=len(trace), n_critiques=len(ctx.state["critique_results"]))

        return traj, traj_path


def _run_enhancer(
    settings: Settings, effective_brief: str, *, skip_enhancer: bool,
) -> EnhancerResult:
    """Run the v2.4 Prompt Enhancer pre-planner stage.

    Returns an `EnhancerResult` either way — when skipped, its
    `enhanced_brief` equals the raw `effective_brief` so the runner can
    use it uniformly as the planner input. API failures also fall back
    to pass-through rather than crashing the run.
    """
    if skip_enhancer or not settings.enable_prompt_enhancer:
        reason = "--skip-enhancer" if skip_enhancer else "disabled in settings"
        log("prompt.enhance.skipped", reason=reason)
        return EnhancerResult(
            enhanced_brief=effective_brief,
            original_brief=effective_brief,
            model=settings.enhancer_model,
            skipped=True,
            skip_reason=reason,
        )
    try:
        system_prompt = load_enhancer_system_prompt(settings)
    except FileNotFoundError as e:
        log("prompt.enhance.missing_prompt", error=str(e),
            fallback="pass-through-raw-brief")
        return EnhancerResult(
            enhanced_brief=effective_brief,
            original_brief=effective_brief,
            model=settings.enhancer_model,
            skipped=True,
            skip_reason="system_prompt_missing",
        )
    enhancer = PromptEnhancer(settings, system_prompt)
    return enhancer.enhance(effective_brief)


def _run_claim_graph_extractor(
    settings: Settings,
    attachments: list[Path],
    *,
    no_claim_graph: bool,
    sub_traj_dir: Path,
) -> ClaimGraph | None:
    """v2.8.0 — extract a `ClaimGraph` from the first attached PDF.

    Skip conditions (any one returns None):
      - `no_claim_graph` (`--no-claim-graph` CLI flag) is True
      - `settings.enable_claim_graph` is False
      - no PDF in attachments
      - PDF text extraction fails
      - extractor returns the sentinel "<extraction failed: timeout>"
        thesis (max_turns / api_error)
      - validator rejects the graph

    Failures are logged but never raise — the planner degrades to
    v2.7.3 chapter-order behavior on any of the above.
    """
    if no_claim_graph:
        log("claim_graph.skipped", reason="--no-claim-graph")
        return None
    if not settings.enable_claim_graph:
        log("claim_graph.skipped", reason="disabled in settings")
        return None

    pdf = next((p for p in attachments if p.suffix.lower() == ".pdf"), None)
    if pdf is None:
        log("claim_graph.skipped", reason="no PDF attachment")
        return None

    try:
        paper_raw_text = _extract_pdf_text_for_claim_graph(pdf)
    except Exception as e:
        log("claim_graph.skipped",
            reason=f"pdf_text_extract_failed: {type(e).__name__}: {e}")
        return None
    if not paper_raw_text or len(paper_raw_text) < 200:
        log("claim_graph.skipped",
            reason="paper_raw_text too short", chars=len(paper_raw_text or ""))
        return None

    trajectory_path = sub_traj_dir / "claim_graph_extractor.jsonl"
    try:
        extractor = ClaimGraphExtractor(settings)
    except Exception as e:
        log("claim_graph.skipped",
            reason=f"extractor_init_failed: {type(e).__name__}: {e}")
        return None

    try:
        graph = extractor.extract(
            paper_path=pdf,
            paper_raw_text=paper_raw_text,
            trajectory_path=trajectory_path,
        )
    except Exception as e:
        log("claim_graph.skipped",
            reason=f"extractor_failed: {type(e).__name__}: {e}")
        return None

    if graph.thesis == EXTRACT_FAIL_THESIS:
        log("claim_graph.degraded",
            reason="extractor returned sentinel thesis (max_turns/api_error)")
        return None

    errors = validate_claim_graph(graph, paper_raw_text)
    if errors:
        log("claim_graph.invalid",
            n_errors=len(errors), first_errors=errors[:3])
        return None

    log("claim_graph.ready",
        thesis_chars=len(graph.thesis),
        n_tensions=len(graph.tensions),
        n_mechanisms=len(graph.mechanisms),
        n_evidence=len(graph.evidence),
        n_implications=len(graph.implications))
    return graph


def _extract_pdf_text_for_claim_graph(pdf: Path) -> str:
    """Cheap text-only PDF extraction. Mirrors the page-text path from
    `tools.ingest_document._ingest_pdf` but skips figure / table / VLM
    work — the extractor only needs raw text to ground evidence quotes."""
    import fitz  # pymupdf

    from .util.pdf import extract_page_text

    doc = fitz.open(pdf)
    try:
        page_texts = extract_page_text(doc)
    finally:
        doc.close()
    return "\n\n".join(page_texts)


def _derive_episode_outcome(
    ctx: ToolContext,
    *,
    finalized: bool,
    spec_present: bool,
    composition_present: bool,
) -> tuple[str, float | None]:
    """Compute (terminal_status, final_reward) from the run's end state.

    - "pass": last critique verdict==pass; final_reward=critique_score
    - "revise": hit max_critique_iters with last verdict==revise; reward=score
    - "fail": last verdict==fail; reward=score (low)
    - "max_turns": no finalize, no spec/composition; reward=None
    - "abort": catch-all when finalize never fired and we have partial state
    """
    crits = ctx.state.get("critique_results") or []
    if crits:
        last = crits[-1]
        score = float(last.score)
        if last.verdict == "pass":
            return "pass", score
        if last.verdict == "revise":
            # finalize after revise → counts as "revise" terminal (not great
            # but the planner stopped; reward signals "kinda OK")
            return "revise", score
        return "fail", score
    if not spec_present:
        return "max_turns", None
    if not composition_present:
        return "abort", None
    # finalize fired but no critique was ever called — count as pass with
    # reward None (no signal). This is rare but possible.
    return "pass" if finalized else "abort", None


def _estimate_cost(
    input_tokens: int, output_tokens: int, *,
    n_critiques: int,
    enhancer_in: int = 0, enhancer_out: int = 0,
) -> float:
    """Rough estimate; tighten after measuring real runs.

    Enhancer is priced at Opus 4.7 rates (that's the default model);
    users who override to a cheaper model will see the cost slightly
    overstated — acceptable for a ballpark."""
    opus_in_per_mtok = 15.0
    opus_out_per_mtok = 75.0
    nbp_per_image_2k = 0.15
    planner_cost = (input_tokens / 1e6) * opus_in_per_mtok + (output_tokens / 1e6) * opus_out_per_mtok
    enhancer_cost = (enhancer_in / 1e6) * opus_in_per_mtok + (enhancer_out / 1e6) * opus_out_per_mtok
    return round(planner_cost + enhancer_cost + nbp_per_image_2k + 0.05 * max(n_critiques, 0), 4)


def _apply_attachment_prologue(brief: str, attachments: list[Path]) -> str:
    """Prefix the brief with an 'Attached files:' block when v1.1 attachments
    are present, instructing the planner to call `ingest_document` first.

    The planner prompt (prompts/planner.md § "Ingestion workflow") teaches
    the model to treat this prefix as a signal.
    """
    if not attachments:
        return brief
    lines = ["Attached files:"]
    for p in attachments:
        try:
            size = p.stat().st_size if p.exists() else 0
        except OSError:
            size = 0
        kb = size // 1024
        lines.append(f"  - {p} ({kb} KB)")
    lines.append(
        "\nCALL `ingest_document` FIRST with these file_paths, THEN write "
        "`propose_design_spec` using the returned manifest (title, sections, "
        "figure layer_ids). Ingested figures are pre-registered in "
        "rendered_layers — reference them by layer_id in your layer_graph."
    )
    return "\n".join(lines) + "\n\n---\n\n" + brief


def _apply_template_prologue(brief: str, template: str | None) -> str:
    """Prefix the brief with a 'Template:' block resolving a registered poster
    template to its canvas preset (w_px / h_px / dpi / aspect_ratio / color_mode).

    The planner sees this as explicit input (same mechanism as attachments),
    reads the resolved dims, and emits them on `DesignSpec.canvas` unchanged
    unless the free-text user brief overrides. Template is validated at the
    CLI before we get here, so an unknown name silently becomes a no-op
    (defensive — don't fail the whole run on a template typo).
    """
    from .config import resolve_template
    canvas = resolve_template(template) if template else None
    if canvas is None:
        return brief
    # Compact one-line serialization so the planner can scan quickly.
    canvas_str = ", ".join(f"{k}={v!r}" for k, v in canvas.items())
    block = (
        f"Template: {template}\n"
        f"  canvas: {canvas_str}\n"
        f"\nThis is a registered academic-poster preset — USE THIS CANVAS "
        f"verbatim on your `DesignSpec.canvas` unless the free-text brief "
        f"explicitly overrides specific dims."
    )
    return block + "\n\n---\n\n" + brief
