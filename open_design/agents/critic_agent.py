"""CriticAgent — v2.7.3 vision critic, forked sub-agent.

Owns its own LLMBackend instance, its own tool loop, its own turn budget,
and its own trajectory file. Architecturally similar to PlannerLoop but
lives outside the planner's loop so the critic can take as many turns as
it needs without consuming planner.max_planner_turns.

Why: the v2.6 inline `critique_tool` shared the planner's turn budget
(max 2 calls), force-injected the entire DesignSpec into a single LLM
call, and used vision only for posters. Cloud Design's separate
vision-verifier agent showed how much fidelity that costs. v2.7.3 splits
the critic into a peer sub-agent that:
  - sees rendered slide PNGs for ALL artifact types (deck/landing/poster)
  - pulls relevant paper passages on-demand instead of dumping raw_text
  - emits a structured `CritiqueReport` via a terminal `report_verdict` tool

The planner-facing tool (`critique_tool.py`) is now a thin wrapper that
spawns one CriticAgent per `critique` invocation. From the planner's
perspective the tool signature is unchanged: one call returns one
CritiqueReport JSON in `tool_result.payload`.

Trajectory: each `CriticAgent.critique()` call appends one JSONL line per
LLM turn to `out/runs/<run_id>/trajectory/critic.jsonl`. Lines are
append-only across iterations so SFT/DPO can replay multi-round critic
behavior without losing history.

Hand-written tool loop, no framework. Same pattern as planner.py.
"""

from __future__ import annotations

import base64
import io
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image
from pydantic import ValidationError

from ..config import Settings
from ..llm_backend import LLMBackend, ToolCall, TurnResponse, make_backend
from ..schema import (
    ArtifactType, ClaimGraph, CritiqueIssue, CritiqueReport, DesignSpec,
    ThinkingBlockRecord,
)
from ..util.io import ensure_dirs
from ..util.logging import log


@dataclass
class CriticTrajectoryRecord:
    """One LLM turn's worth of critic state, written as a JSONL line.

    Mirrors the planner.jsonl line shape so downstream SFT loaders can
    treat both with the same parser.
    """
    iteration: int
    turn: int
    model: str
    backend: str
    artifact_type: str
    thinking_blocks: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    text: str
    stop_reason: str | None
    usage: dict[str, int]


# ─────────────────────────── Tool schemas ──────────────────────────────────

_CRITIC_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "read_slide_render",
        "description": (
            "Fetch a rendered slide PNG by its slide_id (deck) or by the "
            "deck-grid index (poster/landing fall back to a single slide_id "
            "matching the only render available). Returns base64-encoded "
            "PNG bytes that the next turn sees as a vision content block. "
            "Call this whenever you need to inspect the actual rendered "
            "output rather than reasoning over the DesignSpec text only."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "slide_id": {
                    "type": "string",
                    "description": (
                        "The slide_id from DesignSpec.layer_graph (deck) or "
                        "a synthetic id like 'poster_full' / 'landing_full' "
                        "for non-deck artifacts. Match exactly — IDs come "
                        "from the user message's slide manifest."
                    ),
                },
            },
            "required": ["slide_id"],
        },
    },
    {
        "name": "read_paper_section",
        "description": (
            "Pull a relevant excerpt from the source paper raw_text by "
            "keyword / section heading. Returns up to ~2000 chars centered "
            "on the first match. Use this to verify quotes / numbers / "
            "terminology before flagging a provenance issue. Returns empty "
            "string when paper_raw_text is None (free-text brief) or when "
            "no match is found."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "A short keyword, phrase, or section heading to "
                        "search in the paper's raw text. Case-insensitive."
                    ),
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "lookup_claim_node",
        "description": (
            "v2.8.0+ — fetch a single ClaimGraph node by id (T*/M*/E*/I*). "
            "Returns the node's serialized fields so you can verify "
            "whether a slide actually presents that claim. Returns "
            "{\"error\": ...} when no claim_graph is attached or the id "
            "does not exist. Use this when you suspect a tension / "
            "mechanism / evidence node was dropped from the deck."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "claim_id": {
                    "type": "string",
                    "description": (
                        "ClaimGraph node id. Tensions T1/T2/..., "
                        "mechanisms M1/M2/..., evidence E1/E2/..., "
                        "implications I1/I2/..."
                    ),
                },
            },
            "required": ["claim_id"],
        },
    },
    {
        "name": "report_verdict",
        "description": (
            "TERMINAL TOOL. Emit your final CritiqueReport and exit the "
            "loop. Must be called exactly once per critique invocation. "
            "After this call your loop ends; further tool calls are "
            "ignored."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "score": {
                    "type": "number",
                    "description": (
                        "Aggregate quality score in [0, 1]. pass requires "
                        ">= 0.75; fail < 0.5; otherwise revise."
                    ),
                },
                "verdict": {
                    "type": "string",
                    "enum": ["pass", "revise", "fail"],
                },
                "issues": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "slide_id": {"type": ["string", "null"]},
                            "severity": {
                                "type": "string",
                                "enum": ["blocker", "high", "medium", "low"],
                            },
                            "category": {
                                "type": "string",
                                "enum": [
                                    "provenance", "claim_coverage",
                                    "visual_hierarchy", "typography",
                                    "layout", "narrative_flow",
                                    "factual_error",
                                ],
                            },
                            "description": {"type": "string"},
                            "evidence_paper_anchor": {
                                "type": ["string", "null"],
                            },
                        },
                        "required": ["severity", "category", "description"],
                    },
                },
                "summary": {"type": "string"},
            },
            "required": ["score", "verdict", "summary"],
        },
    },
]


_PROMPT_BY_ARTIFACT: dict[ArtifactType, str] = {
    ArtifactType.POSTER: "critic_vision_poster.md",
    ArtifactType.DECK: "critic_vision_deck.md",
    ArtifactType.LANDING: "critic_vision_landing.md",
}


class CriticAgent:
    """Forked vision critic with its own backend + loop.

    One instance per planner-side `critique` invocation. The instance is
    stateless across critique calls — the planner spawns a new
    CriticAgent each round, passing `iteration` so the prompt can adjust
    tone (revise → escalate to fail at last iter).
    """

    def __init__(self, settings: Settings, artifact_type: ArtifactType):
        self.settings = settings
        self.artifact_type = artifact_type
        self.backend: LLMBackend = make_backend(
            settings, settings.critic_model, role="critic",
        )
        self._system_prompt: str | None = None

    def _system(self) -> str:
        if self._system_prompt is None:
            fname = _PROMPT_BY_ARTIFACT[self.artifact_type]
            path: Path = self.settings.prompts_dir / fname
            self._system_prompt = path.read_text(encoding="utf-8")
        return self._system_prompt

    def critique(
        self,
        spec: DesignSpec,
        layer_manifest: list[dict[str, Any]],
        slide_renders: list[Path],
        paper_raw_text: str | None,
        claim_graph: ClaimGraph | None = None,
        iteration: int = 1,
        trajectory_path: Path | None = None,
    ) -> CritiqueReport:
        """Run the sub-agent loop and return the final CritiqueReport.

        On max_turns exhaustion without `report_verdict` being called we
        synthesize a fail verdict so the planner has a deterministic
        signal to react to (instead of hanging or raising).
        """
        slide_index = _index_renders(slide_renders, spec)
        backend_name = self.backend.name
        model = self.backend.model
        log("critic.start", iter=iteration, model=model, backend=backend_name,
            artifact_type=self.artifact_type.value,
            n_renders=len(slide_renders), max_turns=self.settings.critic_max_turns,
            has_paper=paper_raw_text is not None,
            has_claim_graph=claim_graph is not None)
        wall_start = time.monotonic()

        user_text = _build_user_text(
            spec=spec, layer_manifest=layer_manifest,
            slide_index=slide_index, paper_raw_text=paper_raw_text,
            claim_graph=claim_graph, iteration=iteration,
            max_iters=self.settings.max_critique_iters,
        )
        messages: list[Any] = [{"role": "user", "content": user_text}]

        thinking_budget = self.settings.critic_thinking_budget
        max_tokens = max(2048, thinking_budget + 2048) if thinking_budget > 0 else 4096

        terminal_report: CritiqueReport | None = None
        last_response: TurnResponse | None = None

        for turn in range(self.settings.critic_max_turns):
            try:
                resp: TurnResponse = self.backend.create_turn(
                    system=self._system(),
                    messages=messages,
                    tools=_CRITIC_TOOL_SCHEMAS,
                    thinking_budget=thinking_budget,
                    max_tokens=max_tokens,
                )
            except Exception as e:
                log("critic.api_error", iter=iteration, turn=turn + 1,
                    error=f"{type(e).__name__}: {e}")
                terminal_report = _build_failsafe_report(
                    iteration=iteration,
                    summary=f"critic api error: {type(e).__name__}: {e}",
                )
                _append_trajectory(
                    trajectory_path,
                    CriticTrajectoryRecord(
                        iteration=iteration, turn=turn + 1,
                        model=model, backend=backend_name,
                        artifact_type=self.artifact_type.value,
                        thinking_blocks=[],
                        tool_calls=[],
                        tool_results=[{"error": str(e)}],
                        text="", stop_reason="api_error", usage={},
                    ),
                )
                break

            last_response = resp
            self.backend.append_assistant(messages, resp)

            tool_results_for_api: list[tuple[str, str, bool]] = []
            tool_records: list[dict[str, Any]] = []
            for tc in resp.tool_calls:
                payload, is_err, terminal = self._dispatch_tool(
                    tc, slide_index=slide_index,
                    paper_raw_text=paper_raw_text,
                    claim_graph=claim_graph,
                    iteration=iteration,
                )
                tool_results_for_api.append((tc.id, payload, is_err))
                tool_records.append({
                    "id": tc.id, "name": tc.name,
                    "input": _summarize_tool_input(tc.name, tc.input),
                    "is_error": is_err,
                })
                if terminal is not None:
                    terminal_report = terminal

            _append_trajectory(
                trajectory_path,
                CriticTrajectoryRecord(
                    iteration=iteration, turn=turn + 1,
                    model=model, backend=backend_name,
                    artifact_type=self.artifact_type.value,
                    thinking_blocks=[
                        b.model_dump(mode="json") for b in resp.thinking_blocks
                    ],
                    tool_calls=[
                        {"id": tc.id, "name": tc.name,
                         "input": _summarize_tool_input(tc.name, tc.input)}
                        for tc in resp.tool_calls
                    ],
                    tool_results=tool_records,
                    text=resp.text or "",
                    stop_reason=resp.stop_reason,
                    usage=resp.usage or {},
                ),
            )

            if terminal_report is not None:
                break

            if tool_results_for_api:
                self.backend.append_tool_results(messages, tool_results_for_api)
                continue

            if resp.stop_reason == "end_turn":
                log("critic.end_turn_no_verdict",
                    iter=iteration, turn=turn + 1)
                break

        if terminal_report is None:
            terminal_report = _build_failsafe_report(
                iteration=iteration,
                summary=(
                    f"critic max_turns ({self.settings.critic_max_turns}) "
                    "hit without report_verdict; synthesized fail"
                ),
            )

        wall_s = round(time.monotonic() - wall_start, 2)
        usage = (last_response.usage if last_response is not None else {}) or {}
        log("critic.done", iter=iteration, model=model,
            verdict=terminal_report.verdict, score=terminal_report.score,
            n_issues=len(terminal_report.issues),
            input_tokens=usage.get("input", 0),
            output_tokens=usage.get("output", 0),
            wall_s=wall_s)
        return terminal_report

    def _dispatch_tool(
        self,
        tc: ToolCall,
        *,
        slide_index: dict[str, Path],
        paper_raw_text: str | None,
        claim_graph: ClaimGraph | None,
        iteration: int,
    ) -> tuple[str, bool, CritiqueReport | None]:
        """Returns (json_payload, is_error, terminal_report_or_None)."""
        if tc.name == "read_slide_render":
            slide_id = str(tc.input.get("slide_id", ""))
            path = slide_index.get(slide_id)
            if path is None or not path.exists():
                msg = (
                    f"slide_id={slide_id!r} not found. Available: "
                    f"{sorted(slide_index.keys())[:20]}"
                )
                return json.dumps({"error": msg}), True, None
            try:
                b64, media_type = _downscale_b64(
                    path, self.settings.critic_preview_max_edge,
                )
            except Exception as e:
                return json.dumps({"error": f"{type(e).__name__}: {e}"}), True, None
            return (
                json.dumps({
                    "slide_id": slide_id,
                    "media_type": media_type,
                    "image_b64_len": len(b64),
                    "image_b64": b64,
                }),
                False, None,
            )

        if tc.name == "read_paper_section":
            query = str(tc.input.get("query", "")).strip()
            excerpt = _extract_paper_excerpt(paper_raw_text, query)
            return json.dumps({"query": query, "excerpt": excerpt}), False, None

        if tc.name == "lookup_claim_node":
            claim_id = str(tc.input.get("claim_id", "")).strip()
            if claim_graph is None:
                return (
                    json.dumps({
                        "error": "no claim_graph attached to this run",
                        "claim_id": claim_id,
                    }),
                    True, None,
                )
            node = _find_claim_node(claim_graph, claim_id)
            if node is None:
                return (
                    json.dumps({
                        "error": f"unknown claim_id {claim_id!r}",
                        "available_ids": _list_claim_ids(claim_graph),
                    }),
                    True, None,
                )
            return (
                json.dumps({
                    "claim_id": claim_id,
                    "kind": node["kind"],
                    "node": node["node"],
                }, ensure_ascii=False),
                False, None,
            )

        if tc.name == "report_verdict":
            try:
                payload = dict(tc.input)
                payload.setdefault("iteration", iteration)
                payload.setdefault("issues", [])
                report = CritiqueReport.model_validate(payload)
            except ValidationError as e:
                err_msg = (
                    "report_verdict failed schema: "
                    f"{e.errors(include_url=False)[:3]}"
                )
                return json.dumps({"error": err_msg}), True, None
            ack = json.dumps({
                "verdict": report.verdict, "score": report.score,
                "ack": "verdict recorded; loop will exit",
            })
            return ack, False, report

        return (
            json.dumps({"error": f"unknown tool: {tc.name}"}),
            True, None,
        )


# ─────────────────────────── helpers ───────────────────────────────────────


def _index_renders(slide_renders: list[Path], spec: DesignSpec) -> dict[str, Path]:
    """Map slide_id → PNG path so the read_slide_render tool can resolve.

    Deck: pair each slide_renders[i] with the i-th `kind="slide"` node from
    the spec's layer_graph (composite writes them in order). Poster /
    landing: register a single synthetic key (`poster_full` / `landing_full`)
    pointing at the first render path.
    """
    if not slide_renders:
        return {}
    if spec.artifact_type == ArtifactType.DECK:
        slides = [n for n in (spec.layer_graph or [])
                  if getattr(n, "kind", None) == "slide"]
        idx: dict[str, Path] = {}
        for i, render in enumerate(slide_renders):
            if i < len(slides):
                idx[slides[i].layer_id] = render
            idx[f"slide_{i:02d}"] = render
        return idx
    key = ("landing_full" if spec.artifact_type == ArtifactType.LANDING
           else "poster_full")
    return {key: slide_renders[0]}


def _find_claim_node(
    graph: ClaimGraph, claim_id: str,
) -> dict[str, Any] | None:
    """Locate a node by id across all four lists. Returns
    {"kind": "tension"|"mechanism"|"evidence"|"implication",
     "node": <serialized dict>} or None when no match."""
    for tension in graph.tensions:
        if tension.id == claim_id:
            return {"kind": "tension",
                    "node": tension.model_dump(mode="json")}
    for mech in graph.mechanisms:
        if mech.id == claim_id:
            return {"kind": "mechanism",
                    "node": mech.model_dump(mode="json")}
    for ev in graph.evidence:
        if ev.id == claim_id:
            return {"kind": "evidence",
                    "node": ev.model_dump(mode="json")}
    for impl in graph.implications:
        if impl.id == claim_id:
            return {"kind": "implication",
                    "node": impl.model_dump(mode="json")}
    return None


def _list_claim_ids(graph: ClaimGraph) -> dict[str, list[str]]:
    """Compact id catalog for the lookup_claim_node error path."""
    return {
        "tensions": [t.id for t in graph.tensions],
        "mechanisms": [m.id for m in graph.mechanisms],
        "evidence": [e.id for e in graph.evidence],
        "implications": [i.id for i in graph.implications],
    }


def _build_user_text(
    *,
    spec: DesignSpec,
    layer_manifest: list[dict[str, Any]],
    slide_index: dict[str, Path],
    paper_raw_text: str | None,
    claim_graph: ClaimGraph | None,
    iteration: int,
    max_iters: int,
) -> str:
    available_ids = sorted(slide_index.keys())
    paper_blurb = (
        f"paper_raw_text available — {len(paper_raw_text):,} chars. "
        "Use `read_paper_section` to pull excerpts before flagging "
        "provenance issues."
        if paper_raw_text
        else "paper_raw_text NOT available (free-text brief)."
    )
    if claim_graph is not None:
        claim_blurb = (
            "claim_graph: present (v2.8.0). thesis="
            f"{claim_graph.thesis!r}. "
            f"tensions={[t.id for t in claim_graph.tensions]}; "
            f"mechanisms={[m.id for m in claim_graph.mechanisms]}; "
            f"evidence={[e.id for e in claim_graph.evidence]}; "
            f"implications={[i.id for i in claim_graph.implications]}. "
            "Cross-check `slide.covers` against these ids — any "
            "tension/mechanism with no slide.covers reference is a "
            "claim_coverage issue. Use `lookup_claim_node(claim_id)` "
            "to inspect a specific node."
        )
    else:
        claim_blurb = "claim_graph: not available (v2.7.3 baseline)."
    spec_json = json.dumps(spec.model_dump(mode="json"),
                           ensure_ascii=False, indent=2)
    manifest_json = json.dumps(layer_manifest, ensure_ascii=False, indent=2)
    return (
        f"## Critique iteration {iteration} of {max_iters}\n\n"
        f"## Brief\n{spec.brief}\n\n"
        f"## Artifact type\n{spec.artifact_type.value}\n\n"
        f"## Renders available\n"
        f"slide_ids you may pass to `read_slide_render`: "
        f"{available_ids}\n\n"
        f"## Source material\n{paper_blurb}\n{claim_blurb}\n\n"
        f"## DesignSpec snapshot\n```json\n{spec_json}\n```\n\n"
        f"## Composited layer manifest\n```json\n{manifest_json}\n```\n\n"
        "Begin your evaluation. Use `read_slide_render` for each slide you "
        "need to inspect visually, `read_paper_section` to verify any "
        "quotes / numbers, and FINISH with exactly one `report_verdict` "
        "call. Do not emit a verdict in plain text — only via the tool."
    )


def _build_failsafe_report(*, iteration: int, summary: str) -> CritiqueReport:
    return CritiqueReport(
        score=0.0,
        verdict="fail",
        issues=[CritiqueIssue(
            slide_id=None,
            severity="blocker",
            category="layout",
            description=summary,
            evidence_paper_anchor=None,
        )],
        summary=summary,
        iteration=iteration,
    )


def _summarize_tool_input(name: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Strip large blobs (paper text, image base64) from tool inputs before
    serializing into trajectory. Critic tool inputs are small (slide_id /
    query / verdict payload) so this is mostly defensive — keeps the JSONL
    line size bounded if a model ever pastes long content."""
    out = dict(raw or {})
    for key, val in list(out.items()):
        if isinstance(val, str) and len(val) > 1000:
            out[key] = val[:1000] + f"…[truncated {len(val) - 1000} chars]"
    return out


def _append_trajectory(
    path: Path | None,
    record: CriticTrajectoryRecord,
) -> None:
    if path is None:
        return
    ensure_dirs(path.parent)
    line = json.dumps({
        "iteration": record.iteration, "turn": record.turn,
        "model": record.model, "backend": record.backend,
        "artifact_type": record.artifact_type,
        "thinking_blocks": record.thinking_blocks,
        "tool_calls": record.tool_calls,
        "tool_results": record.tool_results,
        "text": record.text, "stop_reason": record.stop_reason,
        "usage": record.usage,
    }, ensure_ascii=False)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _extract_paper_excerpt(raw: str | None, query: str) -> str:
    """Naive substring search with windowed context. Returns up to ~2000
    chars centered on the first hit. Empty string when no match."""
    if not raw or not query:
        return ""
    needle = query.lower()
    haystack_lower = raw.lower()
    pos = haystack_lower.find(needle)
    if pos < 0:
        return ""
    window = 1000
    start = max(0, pos - window)
    end = min(len(raw), pos + len(query) + window)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(raw) else ""
    return f"{prefix}{raw[start:end]}{suffix}"


def _downscale_b64(path: Path, max_edge: int) -> tuple[str, str]:
    """Open `path`, downscale to `max_edge` longest-side, return base64
    JPEG. Mirrors the legacy `critic._downscale_b64` so the new sub-agent
    has the same OOM-safety contract."""
    img = Image.open(path)
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > max_edge:
        if w >= h:
            new = (max_edge, int(h * max_edge / w))
        else:
            new = (int(w * max_edge / h), max_edge)
        img = img.resize(new, Image.LANCZOS)
    if img.mode == "RGBA":
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return base64.b64encode(buf.getvalue()).decode("ascii"), "image/jpeg"
