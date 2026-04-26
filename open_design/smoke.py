"""No-API smoke test: imports + schemas + fonts + a real composite call.

Run with:
    python -m open_design.smoke

Generates `out/smoke/` containing poster.psd, poster.svg, preview.png produced
from a fake (solid-color) background + 2 real text layers rendered via Pillow.
This proves the whole pipeline below the LLM/Gemini layer works without keys.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image
from pydantic import ValidationError

from .schema import (
    AgentTraceStep, CompositionArtifacts, CritiqueResult, DesignSpec,
    DistillTrajectory, LayerNode, SafeZone, TextEffect, ThinkingBlockRecord,
    ToolResultRecord, TrainingMetadata,
)


def _fail(msg: str) -> None:
    print(f"  FAIL  {msg}")
    sys.exit(1)


def _ok(msg: str) -> None:
    print(f"  ok    {msg}")


def check_imports() -> None:
    print("[1/42] imports")
    from . import chat, cli, config, planner, runner, schema, session  # noqa
    from .agents import CriticAgent, PromptEnhancer  # noqa
    from .tools import (
        TOOL_HANDLERS, TOOL_SCHEMAS, ToolContext,
        composite, critique_tool, edit_layer, fetch_brand_asset, finalize,
        generate_background, propose_design_spec, render_text_layer,
        switch_artifact_type,
    )  # noqa
    from .util import ids, io, logging  # noqa
    _ok("all modules import (incl. chat + session + edit_layer)")


def check_tool_registry() -> None:
    print("[2/42] tool registry")
    from .tools import TOOL_HANDLERS, TOOL_SCHEMAS

    expected = {"switch_artifact_type", "propose_design_spec",
                "generate_background", "generate_image",
                "render_text_layer", "edit_layer",
                "fetch_brand_asset", "composite", "critique", "finalize",
                "ingest_document"}
    schema_names = {s["name"] for s in TOOL_SCHEMAS}
    handler_names = set(TOOL_HANDLERS.keys())
    missing = expected - (schema_names & handler_names)
    extra = (schema_names | handler_names) - expected
    if missing:
        _fail(f"missing tools: {missing}")
    if extra:
        _fail(f"unexpected tools: {extra}")
    for s in TOOL_SCHEMAS:
        for k in ("name", "description", "input_schema"):
            if k not in s:
                _fail(f"tool '{s.get('name','?')}' missing key '{k}'")
        if s["input_schema"].get("type") != "object":
            _fail(f"tool '{s['name']}' input_schema.type != 'object'")
    # switch_artifact_type should be first in TOOL_SCHEMAS (pedagogical ordering)
    if TOOL_SCHEMAS[0]["name"] != "switch_artifact_type":
        _fail(f"switch_artifact_type should be first in TOOL_SCHEMAS; got {TOOL_SCHEMAS[0]['name']}")
    _ok(f"{len(expected)} tools wired (schemas + handlers): {sorted(schema_names)}")


def check_pydantic_roundtrip() -> None:
    """v2 trajectory schema roundtrip — covers the new DistillTrajectory
    plus all step types (input / reasoning / tool_call / tool_result /
    finalize), ToolResultRecord (success + error variants), and
    ThinkingBlockRecord (plain + redacted)."""
    print("[3/42] pydantic schema round-trip (v2)")
    plain_thinking = ThinkingBlockRecord(
        thinking="I should declare poster type then propose a 3:4 spec.",
        signature="sig_opaque_anthropic",
        is_redacted=False,
    )
    redacted_thinking = ThinkingBlockRecord(
        thinking="",
        signature="enc_payload_bytes",
        is_redacted=True,
    )
    ok_result = ToolResultRecord(
        status="ok",
        payload={"layer_id": "L1", "sha256": "deadbeef", "width": 1536, "height": 2048},
    )
    err_result = ToolResultRecord(
        status="error",
        error_message="DesignSpec validation failed: missing canvas.w_px",
        error_category="validation",
        payload={"hint": None},
    )
    crit = CritiqueResult(iteration=1, verdict="pass", score=0.86,
                          issues=[], rationale="reads as a coherent poster")
    crit_payload = ToolResultRecord(status="ok", payload=crit.model_dump(mode="json"))

    trace = [
        AgentTraceStep(step_idx=1, actor="user", type="input",
                       text="design a 3:4 poster"),
        AgentTraceStep(step_idx=2, actor="planner", type="reasoning",
                       thinking_blocks=[plain_thinking, redacted_thinking],
                       model="claude-opus-4-7", stop_reason="tool_use",
                       usage={"input": 1234, "output": 200, "cache_read": 1024,
                              "cache_create": 0}),
        AgentTraceStep(step_idx=3, actor="planner", type="tool_call",
                       tool_use_id="toolu_x", tool_name="render_text_layer",
                       tool_args={"layer_id": "L1", "text": "国宝回家"},
                       model="claude-opus-4-7"),
        AgentTraceStep(step_idx=4, actor="tool", type="tool_result",
                       tool_use_id="toolu_x", tool_name="render_text_layer",
                       tool_result=ok_result),
        AgentTraceStep(step_idx=5, actor="planner", type="tool_call",
                       tool_use_id="toolu_y", tool_name="propose_design_spec",
                       tool_args={"design_spec": "stringified-by-mistake"}),
        AgentTraceStep(step_idx=6, actor="tool", type="tool_result",
                       tool_use_id="toolu_y", tool_name="propose_design_spec",
                       tool_result=err_result),
        AgentTraceStep(step_idx=7, actor="planner", type="tool_call",
                       tool_use_id="toolu_z", tool_name="critique"),
        AgentTraceStep(step_idx=8, actor="tool", type="tool_result",
                       tool_use_id="toolu_z", tool_name="critique",
                       tool_result=crit_payload),
        AgentTraceStep(step_idx=9, actor="critic", type="reasoning",
                       thinking_blocks=[plain_thinking],
                       model="claude-opus-4-7"),
        AgentTraceStep(step_idx=10, actor="planner", type="finalize",
                       text="all done"),
    ]

    traj = DistillTrajectory(
        run_id="smoke",
        brief="design a 3:4 poster",
        agent_trace=trace,
        final_reward=0.86,
        terminal_status="pass",
        metadata=TrainingMetadata(
            schema_version="v2",
            planner_model="claude-opus-4-7",
            critic_model="claude-opus-4-7",
            image_model="gemini-3-pro-image-preview",
            planner_thinking_budget=10000,
            critic_thinking_budget=10000,
            interleaved_thinking=True,
            total_input_tokens=1234,
            total_output_tokens=200,
            total_cache_read_tokens=1024,
            total_cache_creation_tokens=0,
            estimated_cost_usd=2.34,
            wall_time_s=187.5,
            source="agent_run",
        ),
    )
    dumped = traj.model_dump(mode="json")
    serialized = json.dumps(dumped, ensure_ascii=False)
    try:
        reloaded = DistillTrajectory.model_validate(json.loads(serialized))
    except ValidationError as e:
        _fail(f"DistillTrajectory round-trip: {e.errors()[:3]}")

    if reloaded.metadata.schema_version != "v2":
        _fail(f"schema_version != v2: {reloaded.metadata.schema_version}")
    if reloaded.final_reward != 0.86:
        _fail(f"final_reward not preserved: {reloaded.final_reward}")
    if reloaded.terminal_status != "pass":
        _fail(f"terminal_status not preserved: {reloaded.terminal_status}")

    reasoning_steps = [s for s in reloaded.agent_trace if s.type == "reasoning"]
    if len(reasoning_steps) != 2:
        _fail(f"expected 2 reasoning steps, got {len(reasoning_steps)}")
    planner_reasoning = next(s for s in reasoning_steps if s.actor == "planner")
    if not planner_reasoning.thinking_blocks or len(planner_reasoning.thinking_blocks) != 2:
        _fail("thinking_blocks lost in roundtrip")
    if planner_reasoning.thinking_blocks[0].thinking != plain_thinking.thinking:
        _fail("thinking text corrupted")
    if not planner_reasoning.thinking_blocks[1].is_redacted:
        _fail("redacted thinking flag lost")
    if planner_reasoning.usage["cache_read"] != 1024:
        _fail("usage.cache_read lost")

    err_step = next(s for s in reloaded.agent_trace
                    if s.tool_result and s.tool_result.status == "error")
    if not err_step.tool_result.error_message:
        _fail("error_message stripped from tool_result")
    if err_step.tool_result.error_category != "validation":
        _fail(f"error_category lost: {err_step.tool_result.error_category}")

    crit_step = next(s for s in reloaded.agent_trace
                     if s.tool_name == "critique" and s.type == "tool_result")
    if crit_step.tool_result.payload.get("score") != 0.86:
        _fail("critique payload (verdict/score/issues) lost")

    _ok(f"DistillTrajectory v2 + 10 trace steps roundtrip ({len(serialized)} bytes)")


def check_fonts() -> None:
    print("[4/42] fonts")
    from PIL import ImageFont
    from .config import REPO_ROOT
    for fname in ("NotoSansSC-Bold.otf", "NotoSerifSC-Bold.otf"):
        path = REPO_ROOT / "assets" / "fonts" / fname
        if not path.exists():
            _fail(f"missing font: {path}")
        try:
            ImageFont.truetype(str(path), size=80)
        except Exception as e:
            _fail(f"font {fname} load failed: {e}")
        _ok(f"loaded {fname} ({path.stat().st_size // 1024} KB)")


def check_composite_no_api() -> None:
    """Build a fake background + 2 real text layers, run composite end-to-end.

    Also exercises switch_artifact_type → propose_design_spec plumbing
    (artifact_type fallback from ctx.state when spec omits it).
    """
    print("[5/42] composite (no API)")
    from .config import REPO_ROOT, Settings
    from .tools import ToolContext
    from .tools.composite import composite
    from .tools.propose_design_spec import propose_design_spec
    from .tools.render_text_layer import render_text_layer
    from .tools.switch_artifact_type import switch_artifact_type

    out_dir = REPO_ROOT / "out" / "smoke"
    layers_dir = out_dir / "layers"
    layers_dir.mkdir(parents=True, exist_ok=True)

    settings = Settings(
        anthropic_api_key="sk-stub",
        anthropic_base_url=None,
        gemini_api_key="stub",
        planner_model="claude-opus-4-7",
        critic_model="claude-opus-4-7",
    )
    ctx = ToolContext(settings=settings, run_dir=out_dir,
                      layers_dir=layers_dir, run_id="smoke")

    # First: switch_artifact_type — verifies ctx.state update + valid type
    obs = switch_artifact_type({"type": "poster"}, ctx=ctx)
    if obs.status != "ok":
        _fail(f"switch_artifact_type: {(obs.error_message or str(obs.payload))}")
    if ctx.state.get("artifact_type") != "poster":
        _fail(f"ctx.state.artifact_type not set; got {ctx.state.get('artifact_type')}")

    # Reject invalid type (paranoia — catches the enum drift)
    bad = switch_artifact_type({"type": "billboard"}, ctx=ctx)
    if bad.status != "error":
        _fail(f"switch_artifact_type should reject 'billboard'; got status={bad.status}")

    # Spec omits artifact_type on purpose — should fall back to ctx.state value
    spec_args = {"design_spec": {
        "brief": "smoke test poster",
        "canvas": {"w_px": 768, "h_px": 1024, "dpi": 150,
                   "aspect_ratio": "3:4", "color_mode": "RGB"},
        "palette": ["#1a1a1a", "#ffffff", "#a02018"],
        "typography": {"title_font": "NotoSerifSC-Bold",
                       "subtitle_font": "NotoSansSC-Bold"},
        "mood": ["test"],
        "composition_notes": "smoke",
        "layer_graph": [],
    }}
    obs = propose_design_spec(spec_args, ctx=ctx)
    if obs.status != "ok":
        _fail(f"propose_design_spec: {(obs.error_message or str(obs.payload))}")

    # Fallback check: spec omitted artifact_type → should inherit ctx.state value
    stored_spec = ctx.state["design_spec"]
    if stored_spec.artifact_type.value != "poster":
        _fail(f"artifact_type fallback failed; got {stored_spec.artifact_type.value!r}")

    bg_path = layers_dir / "bg_smoke.png"
    Image.new("RGB", (768, 1024), (28, 14, 10)).save(bg_path)
    ctx.state["rendered_layers"]["L0_bg"] = {
        "layer_id": "L0_bg", "name": "background", "kind": "background", "z_index": 0,
        "bbox": {"x": 0, "y": 0, "w": 768, "h": 1024},
        "src_path": str(bg_path), "prompt": "(stub)",
        "aspect_ratio": "3:4", "image_size": "1K",
        "safe_zones": [], "sha256": "stub",
    }

    obs = render_text_layer({
        "layer_id": "L1_title", "name": "title", "text": "国宝回家",
        "font_family": "NotoSerifSC-Bold", "font_size_px": 110, "fill": "#fafafa",
        "bbox": {"x": 48, "y": 80, "w": 672, "h": 180}, "align": "center",
        "z_index": 1,
        "effects": {"shadow": {"color": "#00000080", "dx": 0, "dy": 4, "blur": 12}},
    }, ctx=ctx)
    if obs.status != "ok":
        _fail(f"render_text_layer title: {(obs.error_message or str(obs.payload))}")

    obs = render_text_layer({
        "layer_id": "L2_subtitle", "name": "subtitle",
        "text": "National Treasures Return Home",
        "font_family": "NotoSansSC-Bold", "font_size_px": 36, "fill": "#c9a45a",
        "bbox": {"x": 64, "y": 280, "w": 640, "h": 60}, "align": "center",
        "z_index": 2,
    }, ctx=ctx)
    if obs.status != "ok":
        _fail(f"render_text_layer subtitle: {(obs.error_message or str(obs.payload))}")

    obs = composite({}, ctx=ctx)
    if obs.status != "ok":
        _fail(f"composite: {(obs.error_message or str(obs.payload))}")

    comp = ctx.state["composition"]
    for label, p in [("PSD", comp.psd_path), ("SVG", comp.svg_path),
                     ("HTML", comp.html_path), ("preview", comp.preview_path)]:
        if p is None:
            _fail(f"{label} path missing on CompositionArtifacts")
        path = Path(p)
        if not path.exists() or path.stat().st_size == 0:
            _fail(f"{label} not written: {p}")
        _ok(f"{label} {path.name} ({path.stat().st_size // 1024} KB)")


def check_svg_text_is_vector() -> None:
    print("[6/42] SVG + HTML content (vector text, contenteditable, inline fonts)")
    from .config import REPO_ROOT
    # v2.1 versioned layout: composite writes to composites/iter_NN/ and
    # maintains final/ symlinks to the latest iter. Read through final/ so
    # this check stays correct across future iteration counts.
    out_dir = REPO_ROOT / "out" / "smoke" / "final"

    # --- SVG -------------------------------------------------------------
    svg_path = out_dir / "poster.svg"
    if not svg_path.exists():
        _fail("smoke SVG not found — composite step likely failed")
    svg = svg_path.read_text(encoding="utf-8")
    if "<text" not in svg:
        _fail("SVG has no <text> element — text was rasterized!")
    if "国宝回家" not in svg:
        _fail("Chinese title not present as vector text in SVG")
    if "@font-face" not in svg:
        print("  warn  SVG missing @font-face (font subsetting may have failed; "
              "will rely on system fonts)")
    else:
        _ok("SVG: @font-face block embedded with subsetted WOFF2")
    _ok("SVG contains <text>国宝回家</text> as real vector")

    # --- HTML ------------------------------------------------------------
    html_path = out_dir / "poster.html"
    if not html_path.exists():
        _fail("smoke HTML not found — html_renderer step likely failed")
    html_text = html_path.read_text(encoding="utf-8")

    required_markers = {
        "canvas container":     '<div class="canvas"',
        "contenteditable text": 'contenteditable="true"',
        "inline fonts":         "@font-face",
        "WOFF2 data URI":       "data:font/woff2;base64,",
        "data-layer-id attr":   "data-layer-id=",
        "data-kind attr":       "data-kind=",
        "Chinese title text":   "国宝回家",
        "bg data URI":          "data:image/png;base64,",
        "generator meta":       '<meta name="generator" content="OpenDesign"',
        # Edit toolbar markers (v1.0 #6 edit UX)
        "bbox data attrs":      'data-bbox-x=',
        "font-size data attr":  'data-font-size-px=',
        "fill data attr":       'data-fill=',
        "font-family data attr": 'data-font-family=',
        "drag handle":          'class="ld-drag-handle"',
        "toolbar container":    'class="ld-toolbar"',
        "font select":          'id="ld-family"',
        "size input":           'id="ld-size"',
        "color input":          'id="ld-color"',
        "save button":          'id="ld-save"',
        "save modal":           'id="ld-modal-backdrop"',
        "copy button":          'id="ld-copy"',
        "download button":      'id="ld-download"',
        "apply-edits hint":     "open-design apply-edits",
    }
    for label, needle in required_markers.items():
        if needle not in html_text:
            _fail(f"HTML missing expected marker — {label}: {needle!r}")
    _ok(f"HTML poster.html ({html_path.stat().st_size // 1024} KB) — "
        f"all {len(required_markers)} markers present "
        "(canvas / contenteditable / inline fonts+images / data-* attrs)")

    # Structure sanity: exactly one <canvas> div and at least 2 text layers
    if html_text.count('class="canvas"') != 1:
        _fail(f"HTML should contain exactly 1 .canvas div; got "
              f"{html_text.count('class=\"canvas\"')}")
    if html_text.count('class="layer text"') < 2:
        _fail(f"HTML should contain ≥2 text layers (smoke has title + subtitle); "
              f"got {html_text.count('class=\"layer text\"')}")
    _ok("HTML structure: 1 .canvas + ≥2 text layers, all attributed")


def check_chat_session_roundtrip() -> None:
    """ChatSession pydantic + save/load cycle — no API calls."""
    print("[7/42] chat session save/load")
    from .config import REPO_ROOT
    from .session import (
        ChatMessage, ChatSession, TrajectoryRef,
        load_session, new_session_id, save_session, session_path, list_sessions,
    )
    from .schema import ArtifactType

    tmp_dir = REPO_ROOT / "out" / "smoke_sessions"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    sid = new_session_id()
    if not sid.startswith("session_"):
        _fail(f"new_session_id() should start with 'session_'; got {sid!r}")

    # Build a realistic session with 1 user + 1 assistant msg + 1 trajectory ref
    session = ChatSession(session_id=sid)
    session.append_user("design a 3:4 poster for 国宝回家")
    ref = TrajectoryRef(
        run_id="20260418-smoke-test",
        artifact_type=ArtifactType.POSTER,
        created_at=session.created_at,
        trajectory_path="/tmp/fake-trajectory.json",
        preview_path="/tmp/fake-preview.png",
        psd_path="/tmp/fake.psd",
        svg_path="/tmp/fake.svg",
        n_layers=5,
        verdict="pass",
        score=0.86,
        cost_usd=1.41,
        wall_s=100.0,
    )
    session.trajectories.append(ref)
    session.append_assistant("produced poster · 5 layers · pass(0.86)",
                              trajectory_id=ref.run_id)

    path = save_session(session, tmp_dir)
    if not path.exists():
        _fail(f"save_session did not write: {path}")
    _ok(f"saved {path.name} ({path.stat().st_size} bytes)")

    loaded = load_session(tmp_dir, sid)
    if loaded.session_id != sid:
        _fail(f"round-trip: session_id mismatch; {loaded.session_id} != {sid}")
    if len(loaded.message_history) != 2:
        _fail(f"round-trip: message count wrong; {len(loaded.message_history)} != 2")
    if len(loaded.trajectories) != 1 or loaded.trajectories[0].verdict != "pass":
        _fail("round-trip: trajectory ref did not survive")
    _ok(f"round-trip: {len(loaded.message_history)} msgs, "
        f"{len(loaded.trajectories)} trajectory ref(s), cost ${loaded.total_cost_usd()}")

    listing = list_sessions(tmp_dir)
    if sid not in [s[0] for s in listing]:
        _fail("list_sessions did not include the newly-saved session")
    _ok(f"list_sessions finds {len(listing)} session(s) incl. this one")

    # Clean up smoke session file
    path.unlink(missing_ok=True)


def check_edit_layer_no_api() -> None:
    """edit_layer semantics — subset-merge, delegates re-render, refuses non-text."""
    print("[8/42] edit_layer (no API)")
    from .config import REPO_ROOT, Settings
    from .tools import ToolContext
    from .tools.edit_layer import edit_layer
    from .tools.propose_design_spec import propose_design_spec
    from .tools.render_text_layer import render_text_layer
    from .tools.switch_artifact_type import switch_artifact_type

    out_dir = REPO_ROOT / "out" / "smoke_edit"
    layers_dir = out_dir / "layers"
    layers_dir.mkdir(parents=True, exist_ok=True)

    settings = Settings(
        anthropic_api_key="sk-stub",
        anthropic_base_url=None,
        gemini_api_key="stub",
        planner_model="claude-opus-4-7",
        critic_model="claude-opus-4-7",
    )
    ctx = ToolContext(settings=settings, run_dir=out_dir,
                      layers_dir=layers_dir, run_id="smoke_edit")

    # Seed: switch type + spec + one text layer + one fake bg layer
    if switch_artifact_type({"type": "poster"}, ctx=ctx).status != "ok":
        _fail("seed: switch_artifact_type")

    spec_args = {"design_spec": {
        "brief": "edit_layer smoke",
        "canvas": {"w_px": 768, "h_px": 1024, "dpi": 150,
                   "aspect_ratio": "3:4", "color_mode": "RGB"},
        "palette": ["#1a1a1a", "#ffffff"],
        "typography": {"title_font": "NotoSerifSC-Bold",
                       "subtitle_font": "NotoSansSC-Bold"},
        "mood": ["test"], "composition_notes": "", "layer_graph": [],
    }}
    if propose_design_spec(spec_args, ctx=ctx).status != "ok":
        _fail("seed: propose_design_spec")

    # Fake bg layer (non-text — should be refused by edit_layer)
    ctx.state["rendered_layers"]["L0_bg"] = {
        "layer_id": "L0_bg", "name": "background", "kind": "background",
        "z_index": 0, "bbox": {"x": 0, "y": 0, "w": 768, "h": 1024},
        "src_path": "/tmp/fake.png", "sha256": "stub",
    }

    # Real text layer via render_text_layer
    obs = render_text_layer({
        "layer_id": "L1_title", "name": "title", "text": "原标题",
        "font_family": "NotoSerifSC-Bold", "font_size_px": 100, "fill": "#000000",
        "bbox": {"x": 48, "y": 80, "w": 672, "h": 180}, "align": "center",
        "z_index": 1,
    }, ctx=ctx)
    if obs.status != "ok":
        _fail(f"seed: render_text_layer: {(obs.error_message or str(obs.payload))}")

    before = ctx.state["rendered_layers"]["L1_title"]
    before_sha = before["sha256"]
    before_path = Path(before["src_path"])
    if not before_path.exists():
        _fail("seed: initial PNG missing before edit")

    # --- Happy path: multi-field diff -------------------------------------
    obs = edit_layer({
        "layer_id": "L1_title",
        "diff": {"text": "新标题！", "font_size_px": 140, "fill": "#ff0000"},
    }, ctx=ctx)
    if obs.status != "ok":
        _fail(f"edit_layer happy path: status={obs.status} summary={(obs.error_message or str(obs.payload))}")

    after = ctx.state["rendered_layers"]["L1_title"]
    if after["font_size_px"] != 140:
        _fail(f"font_size_px not applied: got {after['font_size_px']}")
    if after["fill"] != "#ff0000":
        _fail(f"fill not applied: got {after['fill']}")
    if after["text"] != "新标题！":
        _fail(f"text not applied: got {after['text']!r}")
    # Unchanged fields preserved
    if after["name"] != "title":
        _fail(f"name should be preserved: got {after['name']!r}")
    if after["align"] != "center":
        _fail(f"align should be preserved: got {after['align']!r}")
    if after["bbox"]["w"] != 672:
        _fail(f"bbox should be preserved: got {after['bbox']}")
    # PNG should have been rewritten (different content → different sha)
    if after["sha256"] == before_sha:
        _fail("PNG sha256 unchanged after edit — render_text_layer didn't re-run")
    _ok("happy path: text+font_size_px+fill applied, other fields preserved, PNG rewritten")

    # --- Partial bbox merge ------------------------------------------------
    obs = edit_layer({
        "layer_id": "L1_title",
        "diff": {"bbox": {"y": 200}},  # only y changes
    }, ctx=ctx)
    if obs.status != "ok":
        _fail(f"bbox partial merge: {(obs.error_message or str(obs.payload))}")
    bbox = ctx.state["rendered_layers"]["L1_title"]["bbox"]
    if not (bbox["x"] == 48 and bbox["y"] == 200 and bbox["w"] == 672 and bbox["h"] == 180):
        _fail(f"bbox partial merge broken: {bbox}")
    _ok("partial bbox merge: y updated, x/w/h preserved")

    # --- Missing layer_id --------------------------------------------------
    obs = edit_layer({"layer_id": "nope", "diff": {"text": "x"}}, ctx=ctx)
    if obs.status != "error" or obs.error_category != "not_found":
        _fail(f"unknown layer should return error/not_found, got status={obs.status} cat={obs.error_category}")
    _ok("unknown layer_id → error/not_found")

    # --- Non-text layer rejected ------------------------------------------
    obs = edit_layer({"layer_id": "L0_bg", "diff": {"text": "x"}}, ctx=ctx)
    if obs.status != "error":
        _fail(f"bg layer edit should error, got {obs.status}")
    if obs.error_category != "validation":
        _fail(f"bg-error category should be validation; got: {obs.error_category}")
    _ok("non-text layer → error/validation")

    # --- Empty / unknown diff fields --------------------------------------
    if edit_layer({"layer_id": "L1_title", "diff": {}}, ctx=ctx).status != "error":
        _fail("empty diff should error")
    if edit_layer({"layer_id": "L1_title",
                   "diff": {"color": "#ff0000"}}, ctx=ctx).status != "error":
        _fail("unknown diff field should error (caught 'color' instead of 'fill')")
    _ok("empty diff + unknown field both rejected")


def check_apply_edits_roundtrip() -> None:
    """HTML → apply-edits → new PSD/SVG/HTML/preview with same semantic content."""
    print("[9/42] apply-edits round-trip (no API)")
    from .apply_edits import apply_edits
    from .config import REPO_ROOT, Settings

    src_html = REPO_ROOT / "out" / "smoke" / "final" / "poster.html"
    if not src_html.exists():
        _fail("smoke HTML missing — [5/16] composite step must run first")

    # Simulate a user edit: bump font size + change color on the title.
    # Re-emit with a changed data-font-size-px/data-fill so round-trip should
    # reflect the new values in the regenerated layer graph.
    raw = src_html.read_text(encoding="utf-8")
    edited = (raw
              .replace('data-font-size-px="110"', 'data-font-size-px="140"')
              .replace('data-fill="#fafafa"', 'data-fill="#ff3366"'))
    if edited == raw:
        _fail("could not seed edits into smoke HTML — assumed markers missing")
    edited_html_path = REPO_ROOT / "out" / "smoke_apply" / "edited.html"
    edited_html_path.parent.mkdir(parents=True, exist_ok=True)
    edited_html_path.write_text(edited, encoding="utf-8")

    settings = Settings(
        anthropic_api_key="sk-stub",
        anthropic_base_url=None,
        gemini_api_key="stub",
        planner_model="claude-opus-4-7",
        critic_model="claude-opus-4-7",
    )
    out_dir = REPO_ROOT / "out" / "smoke_apply" / "restored"

    traj, traj_path, run_dir, restored_ids, skipped = apply_edits(
        edited_html_path, settings=settings, out_dir=out_dir,
    )

    # --- v2 trajectory assertions ----------------------------------------
    if traj.metadata.source != "apply_edits":
        _fail(f"metadata.source != 'apply_edits'; got {traj.metadata.source!r}")
    if traj.agent_trace:
        _fail(f"apply-edits trajectory should have empty agent_trace; got {len(traj.agent_trace)}")
    if traj.terminal_status != "abort":
        _fail(f"apply-edits should set terminal_status=abort; got {traj.terminal_status}")
    _ok(f"trajectory: source=apply_edits, terminal=abort, "
        f"{len(restored_ids)} layers restored")

    # --- artifact files exist --------------------------------------------
    # v2.1 versioned layout: apply_edits' fresh run writes into
    # <run_dir>/composites/iter_01/ with final/ symlinks to the latest iter.
    final_dir = run_dir / "final"
    for fname in ("poster.psd", "poster.svg", "poster.html", "preview.png"):
        p = final_dir / fname
        if not p.exists() or p.stat().st_size == 0:
            _fail(f"{fname} not written: {p}")
    _ok("PSD+SVG+HTML+preview all regenerated in new run_dir")

    # --- edits landed in re-rendered run dir -----------------------------
    # v2 trajectory has no layer_graph; verify the rendered HTML on disk
    # contains the edits instead.
    rendered_html = (final_dir / "poster.html").read_text(encoding="utf-8")
    if "140" not in rendered_html:
        _fail("expected font_size 140 to appear in re-rendered HTML")
    if "#ff3366" not in rendered_html.lower():
        _fail("expected fill #ff3366 to appear in re-rendered HTML")
    _ok("edits preserved in re-rendered HTML: font_size 140, fill #ff3366")

    # --- bg was decoded from data: URI ----------------------------------
    layers_dir = run_dir / "layers"
    bgs = list(layers_dir.glob("bg_*.png")) if layers_dir.exists() else []
    if bgs:
        _ok(f"bg decoded from data URI → {bgs[0].name} ({bgs[0].stat().st_size} B)")


def check_landing_mode() -> None:
    """Landing end-to-end: section-tree spec → HTML + preview → apply-edits roundtrip.

    v1.3: expanded fixture to 4 sections (hero + features + pricing + cta)
    with a `kind="cta"` child, plus a footer-variant section to exercise
    the `<footer>` auto-upgrade. 4 sections triggers auto-nav, and the
    round-trip must preserve CTA nodes with href + variant.
    """
    print("[10/42] landing mode (no API)")
    from .config import REPO_ROOT, Settings
    from .tools import ToolContext
    from .tools.composite import composite
    from .tools.propose_design_spec import propose_design_spec
    from .tools.switch_artifact_type import switch_artifact_type
    from .apply_edits import apply_edits
    from .schema import ArtifactType

    out_dir = REPO_ROOT / "out" / "smoke_landing"
    layers_dir = out_dir / "layers"
    layers_dir.mkdir(parents=True, exist_ok=True)

    settings = Settings(
        anthropic_api_key="sk-stub", anthropic_base_url=None,
        gemini_api_key="stub",
        planner_model="claude-opus-4-7", critic_model="claude-opus-4-7",
    )
    ctx = ToolContext(settings=settings, run_dir=out_dir,
                      layers_dir=layers_dir, run_id="smoke-landing")

    # --- switch + propose landing spec ----------------------------------
    if switch_artifact_type({"type": "landing"}, ctx=ctx).status != "ok":
        _fail("switch_artifact_type(landing)")

    spec_args = {"design_spec": {
        "brief": "OpenDesign v1.0 landing",
        "artifact_type": "landing",
        "canvas": {"w_px": 1200, "h_px": 2400, "dpi": 96,
                   "aspect_ratio": "1:2", "color_mode": "RGB"},
        "palette": ["#0f172a", "#f8fafc"],
        "typography": {"title_font": "NotoSerifSC-Bold", "body_font": "NotoSansSC-Bold"},
        "mood": ["minimal"], "composition_notes": "dark hero, light features, dark cta",
        "layer_graph": [
            {"layer_id": "S1", "name": "hero", "kind": "section", "z_index": 1,
             "children": [
                 {"layer_id": "H1", "name": "hero_headline", "kind": "text", "z_index": 1,
                  "text": "OpenDesign", "font_family": "NotoSerifSC-Bold",
                  "font_size_px": 96, "align": "center",
                  "effects": {"fill": "#f8fafc"}},
                 {"layer_id": "H2", "name": "hero_subhead", "kind": "text", "z_index": 2,
                  "text": "Open source conversational design agent.",
                  "font_family": "NotoSansSC-Bold", "font_size_px": 28,
                  "align": "center", "effects": {"fill": "#94a3b8"}},
             ]},
            {"layer_id": "S2", "name": "features", "kind": "section", "z_index": 2,
             "children": [
                 {"layer_id": "F1", "name": "features_title", "kind": "text", "z_index": 1,
                  "text": "Three outputs, one conversation",
                  "font_family": "NotoSerifSC-Bold", "font_size_px": 48,
                  "effects": {"fill": "#0f172a"}},
                 {"layer_id": "F2", "name": "feature_1", "kind": "text", "z_index": 2,
                  "text": "Poster · PSD + SVG + HTML, fully layered and editable.",
                  "font_family": "NotoSansSC-Bold", "font_size_px": 20,
                  "effects": {"fill": "#334155"}},
             ]},
            {"layer_id": "S3", "name": "pricing", "kind": "section", "z_index": 3,
             "children": [
                 {"layer_id": "P1", "name": "pricing_title", "kind": "text", "z_index": 1,
                  # v2.3 — embed inline + display math so the KaTeX path gets
                  # exercised in smoke. Free-form strings, no rendering happens
                  # at write time; KaTeX is client-side JS.
                  "text": "Pricing model: cost is $O(n)$ per token; total budget $$B = n \\cdot c$$",
                  "font_family": "NotoSerifSC-Bold", "font_size_px": 40,
                  "align": "center", "effects": {"fill": "#0f172a"}},
             ]},
            {"layer_id": "S4", "name": "cta", "kind": "section", "z_index": 4,
             "children": [
                 {"layer_id": "C1", "name": "cta_text", "kind": "text", "z_index": 1,
                  "text": "pip install open-design",
                  "font_family": "NotoSansSC-Bold", "font_size_px": 36,
                  "align": "center", "effects": {"fill": "#f8fafc"}},
                 {"layer_id": "C2", "name": "cta_button", "kind": "cta", "z_index": 2,
                  "text": "Get started", "href": "#sec-features",
                  "variant": "primary"},
             ]},
            {"layer_id": "S5", "name": "footer", "kind": "section", "z_index": 5,
             "children": [
                 {"layer_id": "FT1", "name": "copyright", "kind": "text", "z_index": 1,
                  "text": "© 2026 OpenDesign · MIT",
                  "font_family": "NotoSansSC-Bold", "font_size_px": 14,
                  "align": "center", "effects": {"fill": "#94a3b8"}},
             ]},
        ],
    }}
    if propose_design_spec(spec_args, ctx=ctx).status != "ok":
        _fail("propose_design_spec(landing)")
    if ctx.state["design_spec"].artifact_type != ArtifactType.LANDING:
        _fail("design_spec.artifact_type not LANDING after propose")

    # --- composite landing ----------------------------------------------
    obs = composite({}, ctx=ctx)
    if obs.status != "ok":
        _fail(f"landing composite: {(obs.error_message or str(obs.payload))}")
    comp = ctx.state["composition"]
    if comp.psd_path is not None or comp.svg_path is not None:
        _fail("landing should NOT produce PSD/SVG — got non-None paths")
    for label, p in [("HTML", comp.html_path), ("preview", comp.preview_path)]:
        if not p or not Path(p).exists() or Path(p).stat().st_size == 0:
            _fail(f"landing {label} missing: {p}")
    _ok(f"landing composite: HTML + preview, NO PSD/SVG (correct)")

    # --- HTML structure --------------------------------------------------
    html_text = Path(comp.html_path).read_text(encoding="utf-8")
    required_markers = {
        "landing container":     '<main class="ld-landing"',
        "landing mode meta":     '<meta name="ld-artifact-type" content="landing"',
        "hero section":          'data-section-variant="hero"',
        "features section":      'data-section-variant="features"',
        "cta section":           'data-section-variant="cta"',
        "contenteditable text":  'contenteditable="true"',
        "data-font-size attr":   'data-font-size-px=',
        "toolbar container":     'class="ld-toolbar"',
        "save modal":            'id="ld-modal-backdrop"',
        "landing hides drag":    "/* Drag handle hidden in landing",
        # v1.3 interactive markers
        "auto-nav header":       '<header class="ld-header"',
        "nav anchor":            'data-nav-target="sec-',
        "section id":            'id="sec-',
        "reveal attr":           'data-reveal="true"',
        "cta anchor":             '<a class="ld-cta ld-cta--primary"',
        "cta href":               'href="#sec-features"',
        "cta data-kind":          'data-kind="cta"',
        "semantic footer":        '<footer class="ld-section"',
        "interactive JS":         'IntersectionObserver',
        # v2.3 — KaTeX injected when math delimiters present in any text layer
        "katex stylesheet":       '<style id="ld-katex-css">',
        "katex init":             'renderMathInElement',
        "katex font data URI":    'data:font/woff2;base64',
        "inline math preserved":  'cost is $O(n)$',
        "display math preserved": 'B = n \\cdot c',
    }
    for label, needle in required_markers.items():
        if needle not in html_text:
            _fail(f"landing HTML missing marker — {label}: {needle!r}")
    _ok(f"landing HTML ({Path(comp.html_path).stat().st_size // 1024} KB) — "
        f"all {len(required_markers)} markers present")

    # --- apply-edits round-trip on a seeded-edit landing ---------------
    edited_html = html_text.replace(
        'data-font-size-px="96"', 'data-font-size-px="128"'
    ).replace(
        'data-fill="#f8fafc"', 'data-fill="#38bdf8"', 1  # only first occurrence
    )
    if edited_html == html_text:
        _fail("could not seed landing edits — markers missing")
    edited_path = out_dir / "edited.html"
    edited_path.write_text(edited_html, encoding="utf-8")

    traj, traj_path, run_dir, restored_ids, skipped = apply_edits(
        edited_path, settings=settings,
        out_dir=out_dir / "restored",
    )
    if traj.metadata.source != "apply_edits":
        _fail(f"landing round-trip lost source label: got {traj.metadata.source!r}")
    # v2: trajectory has no design_spec / layer_graph; verify the edits
    # landed in the regenerated HTML on disk instead. v2.1 versioned layout
    # keeps artifacts under composites/iter_NN/ with final/ as the stable
    # symlink surface — read through final/ so we're robust across iters.
    rendered_html = (run_dir / "final" / "index.html").read_text(encoding="utf-8")
    if "128" not in rendered_html:
        _fail("landing edit lost: font_size 128 missing from rendered HTML")
    if "#38bdf8" not in rendered_html.lower():
        _fail("landing edit lost: fill #38bdf8 missing from rendered HTML")
    # CTA must survive (data-* attrs encode href + variant in HTML).
    if 'data-href="#sec-features"' not in rendered_html:
        _fail("CTA href lost on round-trip")
    if 'data-variant="primary"' not in rendered_html:
        _fail("CTA variant lost on round-trip")
    _ok(f"landing round-trip: edits applied (hero_headline 96px → 128px, "
        f"fill→#38bdf8), CTA href+variant intact in regenerated HTML")


def check_design_system_styles() -> None:
    """Render a landing in each of the 6 bundled styles, verify the matching
    CSS got inlined and the style-specific signature tokens are present."""
    print("[11/42] design-system styles (no API)")
    from .config import REPO_ROOT, Settings
    from .tools import ToolContext
    from .tools.composite import composite
    from .tools.propose_design_spec import propose_design_spec
    from .tools.switch_artifact_type import switch_artifact_type

    # A tiny per-style signature: a CSS token string that MUST appear in the
    # rendered HTML only when that style's CSS is loaded.
    style_signatures = {
        "minimalist":     "--ld-shadow-soft",
        "editorial":      "--ld-rule",
        "neubrutalism":   "--ld-shadow-hard",
        "glassmorphism":  "prefers-reduced-transparency",
        "claymorphism":   "--ld-shadow-clay",
        "liquid-glass":   "cubic-bezier(0.34, 1.56",
    }

    base_spec = {
        "brief": "design-system smoke",
        "artifact_type": "landing",
        "canvas": {"w_px": 1200, "h_px": 2400, "dpi": 96,
                   "aspect_ratio": "1:2", "color_mode": "RGB"},
        "palette": ["#0f172a", "#f8fafc"],
        "typography": {"title_font": "NotoSerifSC-Bold", "body_font": "NotoSansSC-Bold"},
        "mood": ["test"], "composition_notes": "",
        "layer_graph": [
            {"layer_id": "S1", "name": "hero", "kind": "section", "z_index": 1,
             "children": [
                 {"layer_id": "H1", "name": "hero_headline", "kind": "text",
                  "z_index": 1, "text": "Test", "font_family": "NotoSansSC-Bold",
                  "font_size_px": 80, "align": "center",
                  "effects": {"fill": "#f8fafc"}},
             ]},
            {"layer_id": "S2", "name": "features", "kind": "section", "z_index": 2,
             "children": [
                 {"layer_id": "F1", "name": "features_title", "kind": "text",
                  "z_index": 1, "text": "Feature",
                  "font_family": "NotoSansSC-Bold", "font_size_px": 36,
                  "effects": {"fill": "#0f172a"}},
             ]},
        ],
    }

    settings = Settings(
        anthropic_api_key="sk-stub", anthropic_base_url=None,
        gemini_api_key="stub",
        planner_model="claude-opus-4-7", critic_model="claude-opus-4-7",
    )

    for style, signature in style_signatures.items():
        out_dir = REPO_ROOT / "out" / "smoke_styles" / style
        layers_dir = out_dir / "layers"
        layers_dir.mkdir(parents=True, exist_ok=True)
        ctx = ToolContext(settings=settings, run_dir=out_dir,
                          layers_dir=layers_dir, run_id=f"smoke-style-{style}")

        if switch_artifact_type({"type": "landing"}, ctx=ctx).status != "ok":
            _fail(f"[{style}] switch_artifact_type")
        spec_args = {"design_spec": {**base_spec,
                                     "design_system": {"style": style}}}
        if propose_design_spec(spec_args, ctx=ctx).status != "ok":
            _fail(f"[{style}] propose_design_spec")
        if composite({}, ctx=ctx).status != "ok":
            _fail(f"[{style}] composite")

        comp = ctx.state["composition"]
        html = Path(comp.html_path).read_text(encoding="utf-8")

        if f'<meta name="ld-design-system" content="{style}">' not in html:
            _fail(f"[{style}] missing design-system meta tag")
        if f'data-ld-style="{style}"' not in html:
            _fail(f"[{style}] missing data-ld-style attr")
        if signature not in html:
            _fail(f"[{style}] CSS signature {signature!r} not inlined — "
                  "assets/design-systems/<style>.css probably not loaded")
        _ok(f"  {style:<14} ({Path(comp.html_path).stat().st_size // 1024} KB) — "
            f"meta + data-attr + CSS signature all present")

    # Final: accent_color override landed in the CSS
    out_dir = REPO_ROOT / "out" / "smoke_styles" / "accent_override"
    layers_dir = out_dir / "layers"
    layers_dir.mkdir(parents=True, exist_ok=True)
    ctx = ToolContext(settings=settings, run_dir=out_dir,
                      layers_dir=layers_dir, run_id="smoke-accent")
    switch_artifact_type({"type": "landing"}, ctx=ctx)
    spec_args = {"design_spec": {**base_spec,
                                 "design_system": {"style": "minimalist",
                                                   "accent_color": "#fe11ba"}}}
    propose_design_spec(spec_args, ctx=ctx)
    composite({}, ctx=ctx)
    html = Path(ctx.state["composition"].html_path).read_text(encoding="utf-8")
    if "--ld-accent: #fe11ba" not in html:
        _fail("accent_color override did not reach CSS (--ld-accent: #fe11ba missing)")
    _ok("accent_color override propagated to --ld-accent token")


def check_landing_with_images() -> None:
    """Landing mode with image children in sections. No NBP call —
    pre-stages a stub PNG in rendered_layers and asserts the renderer
    inlines it + apply-edits round-trips the image layer."""
    print("[12/42] landing with images (no API)")
    from .apply_edits import apply_edits
    from .config import REPO_ROOT, Settings
    from .schema import ArtifactType
    from .tools import ToolContext
    from .tools.composite import composite
    from .tools.propose_design_spec import propose_design_spec
    from .tools.switch_artifact_type import switch_artifact_type

    out_dir = REPO_ROOT / "out" / "smoke_landing_img"
    layers_dir = out_dir / "layers"
    layers_dir.mkdir(parents=True, exist_ok=True)

    # Stub a hero image PNG so generate_image doesn't need to be called.
    hero_img_path = layers_dir / "img_H0_stub.png"
    Image.new("RGB", (600, 800), (244, 200, 180)).save(hero_img_path)
    feat_img_path = layers_dir / "img_F_stub.png"
    Image.new("RGB", (256, 256), (220, 214, 247)).save(feat_img_path)

    settings = Settings(
        anthropic_api_key="sk-stub", anthropic_base_url=None,
        gemini_api_key="stub",
        planner_model="claude-opus-4-7", critic_model="claude-opus-4-7",
    )
    ctx = ToolContext(settings=settings, run_dir=out_dir,
                      layers_dir=layers_dir, run_id="smoke-landing-img")

    # Seed rendered_layers as if generate_image was called
    ctx.state["rendered_layers"]["H0_img"] = {
        "layer_id": "H0_img", "name": "hero_image", "kind": "image",
        "z_index": 1, "bbox": None, "src_path": str(hero_img_path),
        "prompt": "(stub)", "aspect_ratio": "3:4",
        "image_size": "2K", "sha256": "stub",
    }
    ctx.state["rendered_layers"]["F1_img"] = {
        "layer_id": "F1_img", "name": "feature_1_icon", "kind": "image",
        "z_index": 1, "bbox": None, "src_path": str(feat_img_path),
        "prompt": "(stub)", "aspect_ratio": "1:1",
        "image_size": "1K", "sha256": "stub",
    }

    if switch_artifact_type({"type": "landing"}, ctx=ctx).status != "ok":
        _fail("switch_artifact_type")

    spec_args = {"design_spec": {
        "brief": "landing with images test",
        "artifact_type": "landing",
        "design_system": {"style": "claymorphism"},
        "canvas": {"w_px": 1200, "h_px": 2400, "dpi": 96,
                   "aspect_ratio": "1:2", "color_mode": "RGB"},
        "palette": ["#f4f0ea", "#3a2f4a"],
        "typography": {}, "mood": ["test"], "composition_notes": "",
        "layer_graph": [
            {"layer_id": "S1", "name": "hero", "kind": "section", "z_index": 1,
             "children": [
                 {"layer_id": "H0_img", "name": "hero_image", "kind": "image",
                  "z_index": 1, "aspect_ratio": "3:4"},
                 {"layer_id": "H1", "name": "hero_headline", "kind": "text",
                  "z_index": 2, "text": "Test Hero",
                  "font_family": "NotoSerifSC-Bold", "font_size_px": 88,
                  "align": "center", "effects": {"fill": "#3a2f4a"}},
             ]},
            {"layer_id": "S2", "name": "features", "kind": "section", "z_index": 2,
             "children": [
                 {"layer_id": "F1_img", "name": "feature_1_icon", "kind": "image",
                  "z_index": 1, "aspect_ratio": "1:1"},
                 {"layer_id": "F1", "name": "feature_1", "kind": "text",
                  "z_index": 2, "text": "First feature copy.",
                  "font_family": "NotoSansSC-Bold", "font_size_px": 20,
                  "effects": {"fill": "#3a2f4a"}},
             ]},
        ],
    }}
    if propose_design_spec(spec_args, ctx=ctx).status != "ok":
        _fail("propose_design_spec")
    if composite({}, ctx=ctx).status != "ok":
        _fail("composite (with images)")

    comp = ctx.state["composition"]
    html_text = Path(comp.html_path).read_text(encoding="utf-8")

    required = {
        # v2.4.3 shipped drag/resize by appending the `draggable-resizable`
        # class to the figure; omit the closing quote so added classes don't
        # break the substring match.
        "figure.layer.image":     '<figure class="layer image',
        "<img tag":               "<img src=\"data:image/",
        "data-has-image":         'data-has-image="true"',
        "hero image layer_id":    'data-layer-id="H0_img"',
        "feature image layer_id": 'data-layer-id="F1_img"',
        "aspect-ratio data":      'data-aspect-ratio=',
    }
    for label, needle in required.items():
        if needle not in html_text:
            _fail(f"landing+images HTML missing — {label}: {needle!r}")
    _ok(f"landing HTML ({Path(comp.html_path).stat().st_size // 1024} KB) — "
        f"2 <figure> image layers inlined with data URIs")

    # Apply-edits round-trip: reparse HTML, rebuild section tree with images
    traj, _, restored_dir, restored_ids, skipped = apply_edits(
        Path(comp.html_path), settings=settings,
        out_dir=out_dir / "restored",
    )
    # v2: layer info no longer in trajectory; verify the regenerated run_dir
    # has the image PNGs decoded from data: URIs onto disk.
    img_files = sorted((restored_dir / "layers").glob("img_*.png")) \
        if (restored_dir / "layers").exists() else []
    if len(img_files) < 2:
        _fail(f"round-trip lost images: expected ≥2 PNG files, got {len(img_files)}")
    for img in img_files:
        if img.stat().st_size == 0:
            _fail(f"round-trip image is empty: {img}")
    _ok(f"round-trip: {len(img_files)} image layers decoded from data: URI "
        f"into {restored_dir}/layers/")


def check_deck_mode() -> None:
    """Deck end-to-end: slide-tree spec → PPTX + per-slide PNGs + preview grid.
    No API — python-pptx writes a real .pptx that we reopen + verify."""
    print("[13/42] deck mode (no API)")
    from pptx import Presentation as _Reopen

    from .config import REPO_ROOT, Settings
    from .schema import ArtifactType
    from .tools import ToolContext
    from .tools.composite import composite
    from .tools.propose_design_spec import propose_design_spec
    from .tools.switch_artifact_type import switch_artifact_type

    out_dir = REPO_ROOT / "out" / "smoke_deck"
    layers_dir = out_dir / "layers"
    layers_dir.mkdir(parents=True, exist_ok=True)

    # Stub a hero image PNG that one slide embeds, so we exercise the image path.
    hero_png = layers_dir / "img_slide02_hero.png"
    Image.new("RGB", (960, 540), (80, 140, 220)).save(hero_png)

    settings = Settings(
        anthropic_api_key="sk-stub", anthropic_base_url=None,
        gemini_api_key="stub", planner_model="stub", critic_model="stub",
    )
    ctx = ToolContext(settings=settings, run_dir=out_dir, layers_dir=layers_dir,
                      run_id="smoke-deck")

    if switch_artifact_type({"type": "deck"}, ctx=ctx).status != "ok":
        _fail("switch_artifact_type(deck)")

    # Pre-stage the image in rendered_layers so _hydrate_deck_image_srcs can
    # pick it up (mirrors the real two-step generate_image → composite flow).
    ctx.state["rendered_layers"]["img_slide02_hero"] = {
        "layer_id": "img_slide02_hero",
        "name": "hero_image",
        "kind": "image",
        "z_index": 5,
        "bbox": None,
        "src_path": str(hero_png),
        "prompt": "",
        "aspect_ratio": "16:9",
        "image_size": "960x540",
        "sha256": "stub",
    }

    spec = {
        "brief": "Pitch deck smoke — 3 slides: cover, problem with hero image, outro.",
        "artifact_type": "deck",
        "canvas": {"w_px": 1920, "h_px": 1080, "dpi": 96,
                   "aspect_ratio": "16:9", "color_mode": "RGB"},
        "palette": ["#0f172a", "#f8fafc", "#38bdf8"],
        "typography": {"title_font": "NotoSerifSC-Bold",
                       "body_font": "NotoSansSC-Bold"},
        "mood": ["clean", "investor-ready"],
        "composition_notes": "16:9 pitch deck. Title top, body or image below.",
        "layer_graph": [
            {
                "layer_id": "slide_01", "name": "cover", "kind": "slide",
                "z_index": 1,
                "children": [
                    {"layer_id": "slide_01_title", "name": "title",
                     "kind": "text", "z_index": 10,
                     "bbox": {"x": 120, "y": 420, "w": 1680, "h": 160},
                     "text": "MilkCloud",
                     "font_family": "NotoSerifSC-Bold",
                     "font_size_px": 96, "align": "left",
                     "effects": {"fill": "#0f172a"}},
                    {"layer_id": "slide_01_tagline", "name": "tagline",
                     "kind": "text", "z_index": 10,
                     "bbox": {"x": 120, "y": 620, "w": 1680, "h": 80},
                     "text": "the calmest bubble tea brand",
                     "font_family": "NotoSansSC-Bold",
                     "font_size_px": 40, "align": "left",
                     "effects": {"fill": "#64748b"}},
                ],
            },
            {
                "layer_id": "slide_02", "name": "problem_with_image",
                "kind": "slide", "z_index": 2,
                "children": [
                    {"layer_id": "slide_02_title", "name": "title",
                     "kind": "text", "z_index": 10,
                     "bbox": {"x": 120, "y": 80, "w": 1680, "h": 140},
                     "text": "The problem",
                     "font_family": "NotoSerifSC-Bold",
                     "font_size_px": 72, "align": "left",
                     "effects": {"fill": "#0f172a"}},
                    {"layer_id": "img_slide02_hero", "name": "hero_image",
                     "kind": "image", "z_index": 5,
                     "bbox": {"x": 120, "y": 260, "w": 1680, "h": 720},
                     "aspect_ratio": "16:9"},
                ],
                # v2.3 speaker notes — populate notes_slide.notes_text_frame
                "speaker_notes": "Open with a show-of-hands: who had a stressful bubble-tea run this week? Describe the three overlapping signage problems (logos / menu density / color war). Pause 5s on the pain statement. Transition: that's why we built MilkCloud.",
            },
            {
                "layer_id": "slide_03", "name": "thank_you", "kind": "slide",
                "z_index": 3,
                "children": [
                    {"layer_id": "slide_03_title", "name": "title",
                     "kind": "text", "z_index": 10,
                     "bbox": {"x": 120, "y": 440, "w": 1680, "h": 200},
                     "text": "Thank you",
                     "font_family": "NotoSerifSC-Bold",
                     "font_size_px": 128, "align": "center",
                     "effects": {"fill": "#0f172a"}},
                ],
            },
        ],
    }

    obs = propose_design_spec({"design_spec": spec}, ctx=ctx)
    if obs.status != "ok":
        _fail(f"propose_design_spec(deck): {(obs.error_message or str(obs.payload))}")

    obs = composite({}, ctx=ctx)
    if obs.status != "ok":
        _fail(f"composite(deck): {(obs.error_message or str(obs.payload))}")

    comp = ctx.state["composition"]
    if comp.psd_path is not None or comp.svg_path is not None or comp.html_path is not None:
        _fail(f"deck should produce ONLY pptx + preview — got "
              f"psd={comp.psd_path} svg={comp.svg_path} html={comp.html_path}")
    if not comp.pptx_path or not Path(comp.pptx_path).exists():
        _fail(f"deck PPTX missing: {comp.pptx_path}")
    if not comp.preview_path or not Path(comp.preview_path).exists():
        _fail(f"deck preview missing: {comp.preview_path}")

    # Per-slide PNGs exist.
    slides_dir = Path(comp.pptx_path).parent / "slides"
    slide_pngs = sorted(slides_dir.glob("slide_*.png"))
    if len(slide_pngs) != 3:
        _fail(f"expected 3 slide PNGs in {slides_dir}, got {len(slide_pngs)}")
    for p in slide_pngs:
        if p.stat().st_size == 0:
            _fail(f"empty slide PNG: {p}")

    # Reopen with python-pptx and check structure.
    prs = _Reopen(comp.pptx_path)
    if len(prs.slides) != 3:
        _fail(f"pptx reopen: expected 3 slides, got {len(prs.slides)}")

    # Slide 1 should contain both "MilkCloud" and the tagline text as native runs.
    s1_text = " ".join(
        run.text for shape in prs.slides[0].shapes if shape.has_text_frame
        for para in shape.text_frame.paragraphs for run in para.runs
    )
    if "MilkCloud" not in s1_text:
        _fail(f"slide 1 text missing 'MilkCloud' — got: {s1_text!r}")
    if "calmest bubble tea brand" not in s1_text:
        _fail(f"slide 1 tagline missing — got: {s1_text!r}")

    # Slide 2 should contain one picture shape (the hero image).
    pic_count = sum(1 for shape in prs.slides[1].shapes if shape.shape_type == 13)  # MSO_SHAPE_TYPE.PICTURE
    if pic_count != 1:
        _fail(f"slide 2 expected 1 picture shape, got {pic_count}")

    # v2.3 — speaker notes on slide 2 populated notes_slide.notes_text_frame
    s2_notes = prs.slides[1].notes_slide.notes_text_frame.text
    if "show-of-hands" not in s2_notes:
        _fail(f"slide 2 speaker notes missing or wrong — got: {s2_notes[:120]!r}")
    # Slides 1 + 3 (no speaker_notes in fixture) should have empty notes
    s1_notes = prs.slides[0].notes_slide.notes_text_frame.text.strip()
    s3_notes = prs.slides[2].notes_slide.notes_text_frame.text.strip()
    if s1_notes or s3_notes:
        _fail(f"slide 1/3 should have empty notes — got s1={s1_notes!r} s3={s3_notes!r}")

    pptx_size_kb = Path(comp.pptx_path).stat().st_size // 1024
    preview_size_kb = Path(comp.preview_path).stat().st_size // 1024
    _ok(f"deck composite: 3 slides → {pptx_size_kb} KB .pptx + "
        f"3 slide PNGs + {preview_size_kb} KB preview grid")
    _ok("pptx reopen: slide count + native text runs + picture shape + speaker notes all OK")


def check_deck_design_system_template() -> None:
    """v2.5.2 templated deck path: spec with deck_design_system + slide.role +
    template_slot → renders against assets/deck_templates/academic-editorial.pptx.
    Verifies named slots get filled, image_slot gets a real picture, footer +
    slide_number auto-inject, and the original template slides are removed
    from the slide list."""
    print("[21/42] deck design system template (no API)")
    from pptx import Presentation as _Reopen
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    from .config import REPO_ROOT, Settings
    from .schema import (
        ArtifactType, DeckDesignSystem, DesignSpec, LayerNode,
    )
    from .tools import ToolContext
    from .tools.composite import composite

    out_dir = REPO_ROOT / "out" / "smoke_deck_template"
    layers_dir = out_dir / "layers"
    layers_dir.mkdir(parents=True, exist_ok=True)

    # Stub a paper figure on disk so image_slot has something to place.
    fig_path = layers_dir / "ingest_fig_01.png"
    Image.new("RGB", (1200, 800), (180, 220, 200)).save(fig_path)

    settings = Settings(
        anthropic_api_key="sk-stub", anthropic_base_url=None,
        gemini_api_key="stub", planner_model="stub", critic_model="stub",
    )
    ctx = ToolContext(settings=settings, run_dir=out_dir, layers_dir=layers_dir,
                      run_id="smoke-deck-template")

    # Pre-stage the ingested figure in rendered_layers so hydration finds it.
    ctx.state["rendered_layers"]["ingest_fig_01"] = {
        "layer_id": "ingest_fig_01", "name": "method_diagram", "kind": "image",
        "z_index": 5, "bbox": None, "src_path": str(fig_path),
        "aspect_ratio": "3:2",
    }
    ctx.state["design_spec"] = None  # will set below

    spec = DesignSpec(
        brief="LongCat-Next: Lexicalizing Modalities as Discrete Tokens",
        artifact_type=ArtifactType.DECK,
        canvas={"w_px": 1920, "h_px": 1080, "dpi": 96, "aspect_ratio": "16:9", "color_mode": "RGB"},
        deck_design_system=DeckDesignSystem(
            style="academic-editorial",
            footer_text="LongCat-Next · Meituan",
        ),
        layer_graph=[
            LayerNode(layer_id="slide_01", name="cover", kind="slide", z_index=1, role="cover", children=[
                LayerNode(layer_id="slide_01_title", name="title", kind="text", z_index=10,
                          template_slot="title", text="LongCat-Next"),
                LayerNode(layer_id="slide_01_authors", name="authors", kind="text", z_index=10,
                          template_slot="authors", text="Meituan LongCat Team"),
                LayerNode(layer_id="slide_01_badge", name="badge", kind="text", z_index=10,
                          template_slot="badge", text="NeurIPS 2026"),
            ]),
            LayerNode(layer_id="slide_02", name="method", kind="slide", z_index=2,
                      role="content_with_figure", children=[
                LayerNode(layer_id="slide_02_label", name="section_label", kind="text", z_index=10,
                          template_slot="section_label", text="01 · METHOD"),
                LayerNode(layer_id="slide_02_title", name="title", kind="text", z_index=10,
                          template_slot="title", text="DiNA paradigm"),
                LayerNode(layer_id="slide_02_body", name="body", kind="text", z_index=10,
                          template_slot="body", text="Discrete tokens for vision/text/audio."),
                LayerNode(layer_id="ingest_fig_01", name="diagram", kind="image", z_index=5,
                          template_slot="image_slot"),
            ]),
            LayerNode(layer_id="slide_03", name="closing", kind="slide", z_index=3, role="closing",
                      children=[]),
        ],
    )
    ctx.state["design_spec"] = spec

    res = composite({}, ctx=ctx)
    if res.status != "ok":
        _fail(f"composite() returned {res.status}: {res.payload}")

    pptx_path = ctx.state["composition"].pptx_path
    prs = _Reopen(str(pptx_path))
    if len(prs.slides) != 3:
        _fail(f"expected 3 slides after template cleanup, got {len(prs.slides)}")
    _ok(f"templated deck: 3 slides rendered (template's 6 layout slides removed from sldIdLst)")

    # Slide 0 (cover) — title text replaced; badge text replaced; image_slot still shape (no image since cover didn't reference one)
    cover = prs.slides[0]
    cover_texts = {s.name: s.text_frame.text for s in cover.shapes if s.has_text_frame}
    # v2.7.2: cover slides are chrome → no section prefix; title stays exact.
    if cover_texts.get("title") != "LongCat-Next":
        _fail(f"cover title text wrong: {cover_texts.get('title')!r}")
    if cover_texts.get("badge") != "NeurIPS 2026":
        _fail(f"cover badge text wrong: {cover_texts.get('badge')!r}")
    _ok("cover slide: title + badge text filled from template_slot")

    # Slide 1 (content_with_figure) — image placed, footer + slide_number auto-injected.
    # v2.7.2: the default section_number_policy="renumber" prepends "§N · " to
    # the first content slide's title; substring-match instead of equality so
    # the assertion stays orthogonal to the section policy.
    method = prs.slides[1]
    method_texts = {s.name: s.text_frame.text for s in method.shapes if s.has_text_frame}
    if "DiNA paradigm" not in (method_texts.get("title") or ""):
        _fail(f"method title text wrong: {method_texts.get('title')!r}")
    if "LongCat-Next · Meituan" not in (method_texts.get("footer") or ""):
        _fail(f"footer auto-fill missing: {method_texts.get('footer')!r}")
    if method_texts.get("slide_number") != "2/3":
        _fail(f"slide_number auto-fill wrong: expected '2/3' got {method_texts.get('slide_number')!r}")
    pic_count = sum(1 for s in method.shapes if s.shape_type == MSO_SHAPE_TYPE.PICTURE)
    if pic_count != 1:
        _fail(f"expected 1 picture on method slide, got {pic_count}")
    _ok("content_with_figure: title + body filled, image placed, footer + slide_number auto-injected")

    # Slide 2 (closing) — title still says "Thank you" (template default); no footer
    closing = prs.slides[2]
    closing_texts = {s.name: s.text_frame.text for s in closing.shapes if s.has_text_frame}
    if closing_texts.get("title") != "Thank you":
        _fail(f"closing title default wrong: {closing_texts.get('title')!r}")
    _ok("closing slide: template defaults preserved when planner emits no children")


def check_footer_leakage() -> None:
    """v2.5.2.2 hotfix: footer auto-fill must use the ingested paper title,
    NOT the user's brief. Prior bug shipped strings like '12-slide academic
    talk deck for the LongCat-Next paper. Speaker-ready with notes per slide'
    on every content slide footer because the v2.5.2 fallback chain was
    `ds.footer_text or spec.brief[:80]` — and brief is a user command.

    This check builds a spec WITHOUT `ds.footer_text`, pre-stages an
    `ingested` entry on `ctx.state` with manifest.title set, and asserts
    the rendered footer reads the paper title — not the brief, not empty.
    Also asserts the leakage blacklist rejects user-command phrases."""
    print("[22/42] footer leakage check (no API)")
    from pptx import Presentation as _Reopen

    from .config import REPO_ROOT, Settings
    from .schema import (
        ArtifactType, DeckDesignSystem, DesignSpec, LayerNode,
    )
    from .tools import ToolContext
    from .tools.composite import composite
    from .tools.pptx_renderer import _is_leakage, _resolve_footer_text

    # Unit-test the resolver directly (no API, no rendering).
    class _DSStub:
        def __init__(self, ft=None):
            self.footer_text = ft

    class _SpecStub:
        def __init__(self, brief):
            self.brief = brief

    class _CtxStub:
        def __init__(self, state):
            self.state = state

    leak_brief = "12-slide academic talk deck for this paper. Speaker-ready with notes."
    paper_title = "LongCat-Next: Lexicalizing Modalities as Discrete Tokens"

    # Case 1: brief leak with no ingest → empty (NOT brief)
    r = _resolve_footer_text(_DSStub(), _SpecStub(leak_brief), _CtxStub({}))
    if r != "":
        _fail(f"case 1 (no ingest, leak brief): expected empty, got {r!r}")
    # Case 2: ingest title present → use it
    state = {"ingested": [{"manifest": {"title": paper_title}}]}
    r = _resolve_footer_text(_DSStub(), _SpecStub(leak_brief), _CtxStub(state))
    if "LongCat-Next" not in r:
        _fail(f"case 2 (ingest title): expected paper title, got {r!r}")
    # Case 3: explicit clean override wins
    r = _resolve_footer_text(_DSStub("My Paper · Author · ICLR 2026"),
                             _SpecStub(leak_brief), _CtxStub(state))
    if r != "My Paper · Author · ICLR 2026":
        _fail(f"case 3 (explicit clean): got {r!r}")
    # Case 4: explicit dirty override falls through to ingest
    r = _resolve_footer_text(_DSStub("Generate a 12-slide deck for this"),
                             _SpecStub(leak_brief), _CtxStub(state))
    if "LongCat-Next" not in r:
        _fail(f"case 4 (explicit dirty fallthrough): got {r!r}")
    # Blacklist sanity
    assert _is_leakage("Speaker-ready slide deck"), "leakage detect FN"
    assert not _is_leakage("Discrete Tokens for Multimodal AI"), "leakage detect FP"
    _ok("_resolve_footer_text: 4 fallback cases + 2 blacklist cases all pass")

    # Integration: build a 2-slide deck with no footer_text + ingest title,
    # confirm the rendered PPTX content slide footer carries the paper title.
    out_dir = REPO_ROOT / "out" / "smoke_footer_leakage"
    layers_dir = out_dir / "layers"
    layers_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        anthropic_api_key="sk-stub", anthropic_base_url=None,
        gemini_api_key="stub", planner_model="stub", critic_model="stub",
    )
    ctx = ToolContext(settings=settings, run_dir=out_dir, layers_dir=layers_dir,
                      run_id="smoke-footer-leak")
    ctx.state["ingested"] = [{"manifest": {"title": paper_title}}]

    spec = DesignSpec(
        brief=leak_brief,
        artifact_type=ArtifactType.DECK,
        canvas={"w_px": 1920, "h_px": 1080, "dpi": 96, "aspect_ratio": "16:9", "color_mode": "RGB"},
        deck_design_system=DeckDesignSystem(style="academic-editorial"),
        layer_graph=[
            LayerNode(layer_id="slide_01", name="cover", kind="slide", z_index=1, role="cover", children=[
                LayerNode(layer_id="slide_01_title", name="title", kind="text", z_index=10,
                          template_slot="title", text="LongCat-Next"),
            ]),
            LayerNode(layer_id="slide_02", name="content", kind="slide", z_index=2, role="content",
                      children=[
                LayerNode(layer_id="slide_02_title", name="title", kind="text", z_index=10,
                          template_slot="title", text="Method"),
                LayerNode(layer_id="slide_02_body", name="body", kind="text", z_index=10,
                          template_slot="body", text="Body content."),
            ]),
        ],
    )
    ctx.state["design_spec"] = spec

    res = composite({}, ctx=ctx)
    if res.status != "ok":
        _fail(f"composite failed: {res.payload}")
    pptx_path = ctx.state["composition"].pptx_path
    prs = _Reopen(str(pptx_path))
    content_slide = prs.slides[1]
    footer_text = ""
    for shape in content_slide.shapes:
        if shape.has_text_frame and shape.name == "footer":
            footer_text = shape.text_frame.text
            break
    if "LongCat-Next" not in footer_text:
        _fail(f"footer should carry paper title, got {footer_text!r}")
    if _is_leakage(footer_text):
        _fail(f"footer contains leakage phrase: {footer_text!r}")
    _ok(f"rendered footer carries paper title: {footer_text!r}")


def check_callout_overlay() -> None:
    """v2.6 callout system: kind="callout" children render as shapes
    overlaid on top of the anchor picture/table. Verifies all 3 styles
    (highlight / label / circle) plus the optional arrow connector."""
    print("[23/42] callout overlay (no API)")
    from pptx import Presentation as _Reopen
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    from .config import REPO_ROOT, Settings
    from .schema import (
        ArtifactType, DeckDesignSystem, DesignSpec, LayerNode, SafeZone,
    )
    from .tools import ToolContext
    from .tools.composite import composite

    out_dir = REPO_ROOT / "out" / "smoke_callout"
    layers_dir = out_dir / "layers"
    layers_dir.mkdir(parents=True, exist_ok=True)

    fig_path = layers_dir / "ingest_fig_01.png"
    Image.new("RGB", (1200, 800), (180, 220, 200)).save(fig_path)

    settings = Settings(
        anthropic_api_key="sk-stub", anthropic_base_url=None,
        gemini_api_key="stub", planner_model="stub", critic_model="stub",
    )
    ctx = ToolContext(settings=settings, run_dir=out_dir, layers_dir=layers_dir,
                      run_id="smoke-callout")

    ctx.state["rendered_layers"]["ingest_fig_01"] = {
        "layer_id": "ingest_fig_01", "kind": "image", "src_path": str(fig_path),
        "bbox": None, "z_index": 5, "name": "fig", "aspect_ratio": "3:2",
    }

    spec = DesignSpec(
        brief="callout overlay smoke",
        artifact_type=ArtifactType.DECK,
        canvas={"w_px": 1920, "h_px": 1080, "dpi": 96, "aspect_ratio": "16:9", "color_mode": "RGB"},
        deck_design_system=DeckDesignSystem(style="academic-editorial"),
        layer_graph=[
            LayerNode(layer_id="slide_01", name="cover", kind="slide", z_index=1, role="cover", children=[
                LayerNode(layer_id="slide_01_title", name="title", kind="text", z_index=10,
                          template_slot="title", text="Callout Test"),
            ]),
            LayerNode(layer_id="slide_02", name="method", kind="slide", z_index=2,
                      role="content_with_figure", children=[
                LayerNode(layer_id="slide_02_title", name="title", kind="text", z_index=10,
                          template_slot="title", text="Method"),
                LayerNode(layer_id="ingest_fig_01", name="diagram", kind="image", z_index=5,
                          template_slot="image_slot"),
                LayerNode(layer_id="callout_a", name="hl", kind="callout", z_index=20,
                          anchor_layer_id="ingest_fig_01",
                          callout_style="highlight",
                          callout_region=SafeZone(x=1100, y=300, w=400, h=200, purpose="body")),
                LayerNode(layer_id="callout_b", name="lbl", kind="callout", z_index=21,
                          anchor_layer_id="ingest_fig_01",
                          callout_style="label",
                          callout_text="+5.2",
                          callout_region=SafeZone(x=1100, y=300, w=400, h=200, purpose="body"),
                          arrow=True),
                LayerNode(layer_id="callout_c", name="circ", kind="callout", z_index=22,
                          anchor_layer_id="ingest_fig_01",
                          callout_style="circle",
                          callout_region=SafeZone(x=1500, y=600, w=120, h=120, purpose="body")),
            ]),
        ],
    )
    ctx.state["design_spec"] = spec

    res = composite({}, ctx=ctx)
    if res.status != "ok":
        _fail(f"composite failed: {res.payload}")
    pptx_path = ctx.state["composition"].pptx_path
    prs = _Reopen(str(pptx_path))

    method = prs.slides[1]
    names = {s.name for s in method.shapes}
    for required in ("callout_a", "callout_b", "callout_c"):
        if required not in names:
            _fail(f"expected callout '{required}' on method slide; got names={sorted(names)}")
    # Picture should still be there
    pics = [s for s in method.shapes if s.shape_type == MSO_SHAPE_TYPE.PICTURE]
    if len(pics) != 1:
        _fail(f"expected 1 picture on method slide alongside callouts, got {len(pics)}")
    # Arrow connector should exist (named "Connector N" by python-pptx)
    connectors = [s for s in method.shapes if s.name.startswith("Connector")]
    if not connectors:
        _fail("expected at least 1 arrow connector for label callout w/ arrow=True")
    # Highlight callout's bbox should match callout_region (1100,300,400,200) in EMU
    hl = next(s for s in method.shapes if s.name == "callout_a")
    px_to_emu = 9525
    if hl.left != 1100 * px_to_emu or hl.top != 300 * px_to_emu:
        _fail(f"highlight callout EMU position wrong: ({hl.left},{hl.top})")
    _ok(f"3 callouts (highlight + label+arrow + circle) + 1 connector overlaid alongside picture")


def check_provenance_validator() -> None:
    """v2.7 — composite-stage provenance validator audits LayerNode.evidence_quote
    against ingested raw_text. Verifies:
      a. regex extracts the right numeric tokens (and skips safe contexts)
      b. validator passes bullets whose evidence_quote substring-matches
      c. validator fails bullets with no evidence_quote
      d. validator fails bullets whose quote is not in raw_text
      e. apply_strict_provenance replaces failed numeric tokens with [?]
      f. _resolve_authors_text pulls from manifest.authors when planner
         emits placeholder text
      g. _add_table truncates >8-col tables to 6 cols + caption marker
    """
    print("[24/42] provenance validator + cover authors + wide-table cap (no API)")
    from pptx import Presentation as _Reopen

    from .config import REPO_ROOT, Settings
    from .schema import (
        ArtifactType, DeckDesignSystem, DesignSpec, LayerNode,
    )
    from .tools import ToolContext
    from .tools.composite import composite
    from .tools.pptx_renderer import (
        _is_authors_leakage, _resolve_authors_text,
    )
    from .util.provenance import (
        _extract_numeric_tokens, apply_strict_provenance, validate_provenance,
    )

    # ── Part (a): regex sanity ───────────────────────────────────────
    cases = [
        ("MMMU 70.6 vs BAGEL 55.3", ["70.6", "55.3"]),
        ("500K hours of audio", ["500K hours"]),
        ("80 GB vs 120 GB", ["80 GB", "120 GB"]),
        ("PSNR drops 28.5 to 22.1 dB", ["28.5", "22.1 dB"]),
        ("+5.2 pts on MathVista", ["+5.2"]),
        ("40% diversity drop", ["40%"]),
        ("-0.32 exponent", ["-0.32"]),
        ("1/12", []),         # safe — slide index
        ("v2.6.1", []),       # safe — version tag
        ("01 · MOTIVATION", []),  # safe — section label
    ]
    for inp, expected in cases:
        got = _extract_numeric_tokens(inp)
        if got != expected:
            _fail(f"regex {inp!r}: expected {expected}, got {got}")

    # ── Part (b-e): validator + strict mutation ──────────────────────
    paper_title = "LongCat-Next: Lexicalizing Modalities as Discrete Tokens"
    paper_authors = ["Meituan LongCat Team"]
    paper_text = (
        "LongCat-Next achieves 70.6 on MMMU and 83.1 on MathVista, "
        "outperforming BAGEL's 55.3. Table 6 reports PSNR values of "
        "20.88, 21.86, 30.52, 18.16 across configurations."
    )

    spec = DesignSpec(
        brief="provenance smoke",
        artifact_type=ArtifactType.DECK,
        canvas={"w_px": 1920, "h_px": 1080, "dpi": 96, "aspect_ratio": "16:9", "color_mode": "RGB"},
        deck_design_system=DeckDesignSystem(style="academic-editorial"),
        layer_graph=[
            LayerNode(layer_id="s1", name="cover", kind="slide", z_index=1, role="cover", children=[
                LayerNode(layer_id="s1_t", name="title", kind="text", z_index=10,
                          template_slot="title", text=paper_title),
                # Placeholder text — _resolve_authors_text MUST replace it.
                LayerNode(layer_id="s1_a", name="authors", kind="text", z_index=10,
                          template_slot="authors",
                          text="Author One · Author Two · Affiliation"),
            ]),
            LayerNode(layer_id="s2", name="results", kind="slide", z_index=2,
                      role="content", children=[
                LayerNode(layer_id="s2_t", name="title", kind="text", z_index=10,
                          template_slot="title", text="Headline"),
                # ✅ verifiable: "70.6 on MMMU" is a substring of paper_text
                LayerNode(layer_id="s2_b1", name="body", kind="text", z_index=10,
                          template_slot="body",
                          text="MMMU 70.6 vs BAGEL 55.3",
                          evidence_quote="70.6 on MMMU"),
                # ❌ missing quote: numbers must be stripped to [?]
                LayerNode(layer_id="s2_b2", name="body2", kind="text", z_index=10,
                          template_slot="body",
                          text="PSNR drops 28.5 to 22.1 dB"),
                # ❌ quote not in source: numbers must be stripped to [?]
                LayerNode(layer_id="s2_b3", name="body3", kind="text", z_index=10,
                          template_slot="body",
                          text="500K hours of audio in training",
                          evidence_quote="500,000 hours of training audio"),
            ]),
        ],
    )

    out_dir = REPO_ROOT / "out" / "smoke_provenance"
    layers_dir = out_dir / "layers"
    layers_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        anthropic_api_key="sk-stub", anthropic_base_url=None,
        gemini_api_key="stub", planner_model="stub", critic_model="stub",
    )
    ctx = ToolContext(settings=settings, run_dir=out_dir, layers_dir=layers_dir,
                      run_id="smoke-prov")
    ctx.state["ingested"] = [{
        "file": "test.pdf", "type": "pdf",
        "manifest": {"title": paper_title, "authors": paper_authors},
        "raw_text": paper_text,
    }]

    rep = validate_provenance(spec, ctx)
    if rep.n_passed != 1:
        _fail(f"expected 1 pass, got {rep.n_passed}")
    if len(rep.failures) != 2:
        _fail(f"expected 2 failures, got {len(rep.failures)}: {rep.failures}")
    fail_ids = {f.layer_id for f in rep.failures}
    if fail_ids != {"s2_b2", "s2_b3"}:
        _fail(f"failure ids: expected {{s2_b2,s2_b3}}, got {fail_ids}")
    reasons = {f.reason for f in rep.failures}
    if reasons != {"missing_quote", "quote_not_in_source"}:
        _fail(f"reasons: expected both, got {reasons}")

    n_mut = apply_strict_provenance(spec, rep)
    if n_mut != 2:
        _fail(f"expected 2 mutations, got {n_mut}")
    s2 = spec.layer_graph[1]
    b2 = s2.children[2].text
    b3 = s2.children[3].text
    if "[?]" not in b2 or "28.5" in b2 or "22.1" in b2:
        _fail(f"b2 not stripped: {b2!r}")
    if "[?]" not in b3 or "500K" in b3:
        _fail(f"b3 not stripped: {b3!r}")
    _ok("validator: 1 pass + 2 failures (missing/mismatch) → strict mutation [?] correct")

    # ── Part (f): _resolve_authors_text ──────────────────────────────
    if not _is_authors_leakage("Author One · Author Two · Affiliation"):
        _fail("leakage detect FN: 'Author One' should be flagged")
    if _is_authors_leakage("Meituan LongCat Team"):
        _fail("leakage detect FP: real team name flagged")
    r = _resolve_authors_text(ctx, existing="Author One · Affiliation")
    if r != "Meituan LongCat Team":
        _fail(f"authors fallthrough: expected manifest, got {r!r}")
    r2 = _resolve_authors_text(ctx, existing="Meituan LongCat Team")
    if r2 != "Meituan LongCat Team":
        _fail(f"authors keep: expected real-name kept, got {r2!r}")
    _ok("_resolve_authors_text: leakage detection + manifest fallthrough OK")

    # ── Part (g): wide-table cap via composite integration ───────────
    # Re-build the spec since we mutated it above; add a wide table slide.
    wide_headers = ["Model"] + [f"B{i}" for i in range(11)]  # 12 cols
    wide_rows = [
        ["LongCat-Next"] + [f"{70+i}.{i}" for i in range(11)],
        ["BAGEL"] + [f"{50+i}.{i}" for i in range(11)],
    ]
    spec2 = DesignSpec(
        brief="wide-table smoke",
        artifact_type=ArtifactType.DECK,
        canvas={"w_px": 1920, "h_px": 1080, "dpi": 96, "aspect_ratio": "16:9", "color_mode": "RGB"},
        deck_design_system=DeckDesignSystem(style="academic-editorial"),
        layer_graph=[
            LayerNode(layer_id="s1", name="cover", kind="slide", z_index=1, role="cover", children=[
                LayerNode(layer_id="s1_t", name="title", kind="text", z_index=10,
                          template_slot="title", text=paper_title),
                LayerNode(layer_id="s1_a", name="authors", kind="text", z_index=10,
                          template_slot="authors",
                          text="Author One · Author Two · Affiliation"),
            ]),
            LayerNode(layer_id="s2", name="wide", kind="slide", z_index=2,
                      role="content_with_table", children=[
                LayerNode(layer_id="s2_t", name="title", kind="text", z_index=10,
                          template_slot="title", text="Wide Table"),
                LayerNode(layer_id="s2_tab", name="tab", kind="table", z_index=5,
                          template_slot="table_anchor",
                          headers=wide_headers, rows=wide_rows),
            ]),
        ],
    )
    ctx.state["design_spec"] = spec2
    res = composite({}, ctx=ctx)
    if res.status != "ok":
        _fail(f"composite failed: {res.payload}")
    pptx_path = ctx.state["composition"].pptx_path
    prs = _Reopen(str(pptx_path))

    # Cover authors: planner placeholder must be overridden by manifest
    cover = prs.slides[0]
    authors_text = ""
    for shape in cover.shapes:
        if shape.has_text_frame and shape.name == "authors":
            authors_text = shape.text_frame.text
            break
    if "Meituan LongCat Team" not in authors_text:
        _fail(f"cover authors not auto-resolved: got {authors_text!r}")
    if "Author One" in authors_text:
        _fail(f"cover authors leak: {authors_text!r}")

    # Wide table: rendered table should have ≤ 8 cols
    wide_slide = prs.slides[1]
    tables = [s for s in wide_slide.shapes if s.has_table]
    if not tables:
        _fail("wide-table slide missing rendered table")
    n_cols_rendered = len(tables[0].table.columns)
    if n_cols_rendered > 8:
        _fail(f"wide-table cap not enforced: rendered {n_cols_rendered} cols")
    if n_cols_rendered != 6:
        _fail(f"wide-table cap should keep 6 cols, got {n_cols_rendered}")

    # provenance_report.json should exist (from spec2 — no number bullets,
    # so report is clean; but file must be written when ingested present)
    import json
    iter_dirs = sorted((ctx.run_dir / "composites").glob("iter_*"))
    last_iter = iter_dirs[-1]
    report_path = last_iter / "provenance_report.json"
    if not report_path.exists():
        _fail(f"provenance_report.json missing at {report_path}")
    rep_data = json.loads(report_path.read_text())
    if "n_audited" not in rep_data:
        _fail(f"provenance_report.json missing keys: {list(rep_data.keys())}")
    _ok(f"composite integration: cover auto-fills {authors_text!r}, "
        f"wide table 12→{n_cols_rendered} cols, provenance_report.json written")


def check_reasoning_step_roundtrip() -> None:
    """v2 training-data: derive artifact_type / design_spec / critique
    verdict / layer count from agent_trace alone (no top-level fields).

    Asserts the chat.py helpers (_last_artifact_type / _last_design_spec /
    _last_critique_payload / _count_unique_layers) correctly recover state
    from a synthetic v2 trajectory shape.
    """
    print("[14/42] v2 trajectory: derive metadata from agent_trace only")
    from .chat import (
        _last_artifact_type, _last_design_spec, _last_critique_payload,
        _count_unique_layers,
    )

    crit_payload = {
        "iteration": 1, "verdict": "pass", "score": 0.86,
        "issues": [], "rationale": "ok",
    }
    spec_input = {
        "brief": "test", "artifact_type": "landing",
        "canvas": {"w_px": 1200, "h_px": 2400, "dpi": 96,
                   "aspect_ratio": "1:2", "color_mode": "RGB"},
        "layer_graph": [],
    }

    trace = [
        AgentTraceStep(step_idx=1, actor="user", type="input", text="brief"),
        AgentTraceStep(step_idx=2, actor="planner", type="tool_call",
                       tool_use_id="t1", tool_name="switch_artifact_type",
                       tool_args={"type": "landing"}),
        AgentTraceStep(step_idx=3, actor="tool", type="tool_result",
                       tool_use_id="t1", tool_name="switch_artifact_type",
                       tool_result=ToolResultRecord(status="ok",
                                                   payload={"type": "landing"})),
        AgentTraceStep(step_idx=4, actor="planner", type="tool_call",
                       tool_use_id="t2", tool_name="propose_design_spec",
                       tool_args={"design_spec": spec_input}),
        AgentTraceStep(step_idx=5, actor="planner", type="tool_call",
                       tool_use_id="t3", tool_name="generate_image",
                       tool_args={"layer_id": "L1", "prompt": "hero"}),
        AgentTraceStep(step_idx=6, actor="planner", type="tool_call",
                       tool_use_id="t4", tool_name="render_text_layer",
                       tool_args={"layer_id": "L2", "text": "Hello"}),
        AgentTraceStep(step_idx=7, actor="planner", type="tool_call",
                       tool_use_id="t5", tool_name="critique"),
        AgentTraceStep(step_idx=8, actor="tool", type="tool_result",
                       tool_use_id="t5", tool_name="critique",
                       tool_result=ToolResultRecord(status="ok",
                                                   payload=crit_payload)),
    ]
    traj = DistillTrajectory(
        run_id="smoke-v2-helpers",
        brief="brief",
        agent_trace=trace,
        final_reward=0.86,
        terminal_status="pass",
        metadata=TrainingMetadata(
            schema_version="v2",
            planner_model="m", critic_model="m", image_model="m",
            planner_thinking_budget=0, critic_thinking_budget=0,
            interleaved_thinking=False,
            total_input_tokens=0, total_output_tokens=0,
            estimated_cost_usd=0.0, wall_time_s=0.0,
            source="agent_run",
        ),
    )

    if _last_artifact_type(traj) != "landing":
        _fail(f"_last_artifact_type: {_last_artifact_type(traj)}")
    spec = _last_design_spec(traj)
    if not spec or spec.get("artifact_type") != "landing":
        _fail(f"_last_design_spec lost: {spec}")
    crit = _last_critique_payload(traj)
    if not crit or crit.get("score") != 0.86:
        _fail(f"_last_critique_payload lost: {crit}")
    n = _count_unique_layers(traj)
    if n != 2:
        _fail(f"_count_unique_layers: expected 2 (L1, L2), got {n}")

    _ok("artifact_type, design_spec, critique payload, layer count "
        "all recoverable from agent_trace alone")


def check_ingest_document_markdown() -> None:
    """Markdown ingestion: seed a stub .md with a relative image ref, verify
    ingest_document registers the image in rendered_layers + returns the raw
    text. No API — markdown path doesn't call Anthropic."""
    print("[15/42] ingest_document markdown (no API)")
    from .config import REPO_ROOT, Settings
    from .tools import ToolContext
    from .tools.ingest_document import ingest_document

    out_dir = REPO_ROOT / "out" / "smoke_ingest_md"
    layers_dir = out_dir / "layers"
    layers_dir.mkdir(parents=True, exist_ok=True)

    # Stage a stub MD with an embedded image reference (relative path).
    src_dir = out_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    img_path = src_dir / "diagram.png"
    Image.new("RGB", (640, 360), (90, 130, 200)).save(img_path)
    md_path = src_dir / "notes.md"
    md_path.write_text(
        "# My project notes\n\n"
        "## Overview\nA few thoughts below.\n\n"
        "![System diagram](diagram.png)\n\n"
        "## Plans\n- Ship v1\n- Iterate\n"
        "\n![Missing](./nowhere.png)\n",
        encoding="utf-8",
    )

    settings = Settings(
        anthropic_api_key="sk-stub", anthropic_base_url=None,
        gemini_api_key="stub", planner_model="stub", critic_model="stub",
    )
    ctx = ToolContext(settings=settings, run_dir=out_dir, layers_dir=layers_dir,
                      run_id="smoke-ingest-md")

    obs = ingest_document({"file_paths": [str(md_path)]}, ctx=ctx)
    if obs.status != "ok":
        _fail(f"ingest_document(markdown): {(obs.error_message or str(obs.payload))}")

    ingested = ctx.state.get("ingested") or []
    if len(ingested) != 1 or ingested[0]["type"] != "markdown":
        _fail(f"ingested manifest missing markdown entry: {ingested}")
    registered = ingested[0]["registered_layer_ids"]
    skipped = ingested[0]["skipped_images"]
    if len(registered) != 1:
        _fail(f"expected 1 image layer registered from MD, got {registered}")
    if len(skipped) != 1:
        _fail(f"expected 1 skipped bad ref, got {skipped}")

    layer_id = registered[0]
    rec = ctx.state["rendered_layers"].get(layer_id)
    if not rec or not rec.get("src_path") or not Path(rec["src_path"]).exists():
        _fail(f"markdown-registered image not hydrated: {rec}")
    _ok(f"markdown ingested: {ingested[0]['n_chars']} chars, "
        f"1 image layer ({layer_id}), 1 bad ref skipped")


def check_ingest_document_image() -> None:
    """Standalone image ingestion: seed a PNG, verify ingest_document copies
    into layers_dir + registers a passthrough layer with correct shape."""
    print("[16/42] ingest_document image (no API)")
    from .config import REPO_ROOT, Settings
    from .tools import ToolContext
    from .tools.ingest_document import ingest_document

    out_dir = REPO_ROOT / "out" / "smoke_ingest_image"
    layers_dir = out_dir / "layers"
    layers_dir.mkdir(parents=True, exist_ok=True)

    src_dir = out_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    img_path = src_dir / "logo.png"
    Image.new("RGB", (512, 512), (240, 80, 60)).save(img_path)

    settings = Settings(
        anthropic_api_key="sk-stub", anthropic_base_url=None,
        gemini_api_key="stub", planner_model="stub", critic_model="stub",
    )
    ctx = ToolContext(settings=settings, run_dir=out_dir, layers_dir=layers_dir,
                      run_id="smoke-ingest-image")

    obs = ingest_document({"file_paths": [str(img_path)]}, ctx=ctx)
    if obs.status != "ok":
        _fail(f"ingest_document(image): {(obs.error_message or str(obs.payload))}")

    ingested = ctx.state.get("ingested") or []
    if len(ingested) != 1 or ingested[0]["type"] != "image":
        _fail(f"ingested manifest missing image entry: {ingested}")
    if (ingested[0]["width"], ingested[0]["height"]) != (512, 512):
        _fail(f"image dims not captured: {ingested[0]}")

    registered = ingested[0]["registered_layer_ids"]
    if len(registered) != 1:
        _fail(f"expected 1 image layer, got {registered}")
    layer_id = registered[0]
    rec = ctx.state["rendered_layers"].get(layer_id)
    if rec["kind"] != "image":
        _fail(f"image layer kind != image: {rec['kind']}")
    if rec.get("source") != "ingested":
        _fail(f"image source tag wrong: {rec.get('source')}")
    if not Path(rec["src_path"]).exists():
        _fail(f"copied image path missing: {rec['src_path']}")
    _ok(f"image ingested: {layer_id}, 512×512, source=ingested, "
        f"sha256[:8]={rec['sha256'][:8]}")


def check_ingest_document_docx() -> None:
    """Docx ingestion (v1.2.5): build a minimal Word doc with headings +
    an inline image, verify ingest_document extracts sections + figures
    without any VLM call."""
    print("[17/42] ingest_document docx (no API)")
    from docx import Document
    from docx.shared import Inches
    from .config import REPO_ROOT, Settings
    from .tools import ToolContext
    from .tools.ingest_document import ingest_document

    out_dir = REPO_ROOT / "out" / "smoke_ingest_docx"
    layers_dir = out_dir / "layers"
    layers_dir.mkdir(parents=True, exist_ok=True)

    src_dir = out_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    img_path = src_dir / "icon.png"
    Image.new("RGB", (64, 64), (200, 80, 80)).save(img_path)

    docx_path = src_dir / "notes.docx"
    doc = Document()
    doc.add_heading("Quick Tour", level=0)
    doc.add_heading("Motivation", level=1)
    doc.add_paragraph("First thought about the project. Second thought.")
    doc.add_paragraph("- Short bullet.")
    doc.add_heading("Architecture", level=1)
    doc.add_paragraph("Planner + critic + composer.")
    doc.add_picture(str(img_path), width=Inches(1.0))
    doc.save(str(docx_path))

    settings = Settings(
        anthropic_api_key="sk-stub", anthropic_base_url=None,
        gemini_api_key="stub", planner_model="stub", critic_model="stub",
    )
    ctx = ToolContext(settings=settings, run_dir=out_dir, layers_dir=layers_dir,
                      run_id="smoke-ingest-docx")

    obs = ingest_document({"file_paths": [str(docx_path)]}, ctx=ctx)
    if obs.status != "ok":
        _fail(f"ingest_document(docx): {(obs.error_message or str(obs.payload))}")

    ingested = ctx.state.get("ingested") or []
    if len(ingested) != 1 or ingested[0]["type"] != "docx":
        _fail(f"ingested manifest missing docx entry: {ingested}")
    m = ingested[0]["manifest"]
    if m["title"] != "Quick Tour":
        _fail(f"docx title wrong: {m['title']!r}")
    headings = [s["heading"] for s in m["sections"]]
    if headings != ["Motivation", "Architecture"]:
        _fail(f"docx sections wrong: {headings}")
    figure_ids = ingested[0]["registered_figure_ids"]
    if len(figure_ids) != 1 or not figure_ids[0].startswith("ingest_fig_"):
        _fail(f"docx figure registration wrong: {figure_ids}")
    rec = ctx.state["rendered_layers"][figure_ids[0]]
    if rec.get("source") != "ingested_docx" or not Path(rec["src_path"]).exists():
        _fail(f"docx figure record wrong: {rec}")
    _ok(f"docx ingested: title='{m['title']}', "
        f"{len(m['sections'])} section(s), 1 figure ({figure_ids[0]})")


def check_ingest_document_pptx() -> None:
    """Pptx ingestion (v1.2.5): build a 2-slide PowerPoint with a title,
    body bullets, and an embedded picture; verify slides become sections
    and the picture becomes an ingest_fig_NN layer."""
    print("[18/42] ingest_document pptx (no API)")
    from pptx import Presentation
    from pptx.util import Inches
    from .config import REPO_ROOT, Settings
    from .tools import ToolContext
    from .tools.ingest_document import ingest_document

    out_dir = REPO_ROOT / "out" / "smoke_ingest_pptx"
    layers_dir = out_dir / "layers"
    layers_dir.mkdir(parents=True, exist_ok=True)

    src_dir = out_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    img_path = src_dir / "chart.png"
    Image.new("RGB", (128, 128), (50, 160, 90)).save(img_path)

    pptx_path = src_dir / "deck.pptx"
    prs = Presentation()
    s1 = prs.slides.add_slide(prs.slide_layouts[0])
    s1.shapes.title.text = "OpenDesign Pitch"
    s1.placeholders[1].text = "Open-source conversational design agent"
    s2 = prs.slides.add_slide(prs.slide_layouts[1])
    s2.shapes.title.text = "Why now?"
    s2.placeholders[1].text = "Claude Design shipped. We go terminal-first."
    s2.shapes.add_picture(str(img_path), Inches(4), Inches(2), width=Inches(2))
    prs.save(str(pptx_path))

    settings = Settings(
        anthropic_api_key="sk-stub", anthropic_base_url=None,
        gemini_api_key="stub", planner_model="stub", critic_model="stub",
    )
    ctx = ToolContext(settings=settings, run_dir=out_dir, layers_dir=layers_dir,
                      run_id="smoke-ingest-pptx")

    obs = ingest_document({"file_paths": [str(pptx_path)]}, ctx=ctx)
    if obs.status != "ok":
        _fail(f"ingest_document(pptx): {(obs.error_message or str(obs.payload))}")

    ingested = ctx.state.get("ingested") or []
    if len(ingested) != 1 or ingested[0]["type"] != "pptx":
        _fail(f"ingested manifest missing pptx entry: {ingested}")
    m = ingested[0]["manifest"]
    if m["title"] != "OpenDesign Pitch":
        _fail(f"pptx title wrong: {m['title']!r}")
    if len(m["sections"]) != 2:
        _fail(f"pptx slides→sections wrong: {[s['heading'] for s in m['sections']]}")
    figure_ids = ingested[0]["registered_figure_ids"]
    if len(figure_ids) != 1 or not figure_ids[0].startswith("ingest_fig_"):
        _fail(f"pptx figure registration wrong: {figure_ids}")
    rec = ctx.state["rendered_layers"][figure_ids[0]]
    if rec.get("source") != "ingested_pptx" or rec.get("source_ref") != "slide=2":
        _fail(f"pptx figure record wrong: {rec}")
    _ok(f"pptx ingested: title='{m['title']}', "
        f"2 slide(s), 1 figure ({figure_ids[0]})")


def check_sub_figure_registration() -> None:
    """v2.3 sub-figure extraction: VLM detects multi-panel composite figures
    and we Pillow-crop each panel into its own `ingest_fig_NN_<label>` layer.
    Tests the registration helper directly (no API) with a synthetic
    2-panel composite PNG + a stub VLM response.

    Asserts:
    - 2 panel entries → 2 new rendered_layers records (parent stays)
    - Each panel's crop file exists on disk with correct dims
    - parent_layer_id breadcrumb set on children
    - Layer_id naming convention `ingest_fig_NN_<label>` holds
    """
    print("[19/42] sub-figure extraction (no API)")
    from .config import REPO_ROOT, Settings
    from .tools import ToolContext
    from .tools.ingest_document import _register_sub_panels

    out_dir = REPO_ROOT / "out" / "smoke_sub_figs"
    layers_dir = out_dir / "layers"
    if out_dir.exists():
        import shutil
        shutil.rmtree(out_dir)
    layers_dir.mkdir(parents=True, exist_ok=True)

    # Synthesize a composite parent: 600×300 image split into two
    # horizontal halves (panel a on left, panel b on right).
    parent_path = layers_dir / "img_ingest_fig_01.png"
    composite_img = Image.new("RGB", (600, 300), (255, 255, 255))
    # Left half red, right half blue — easy to eyeball crop correctness
    left = Image.new("RGB", (300, 300), (200, 60, 60))
    right = Image.new("RGB", (300, 300), (60, 100, 200))
    composite_img.paste(left, (0, 0))
    composite_img.paste(right, (300, 0))
    composite_img.save(parent_path, "PNG")

    settings = Settings(
        anthropic_api_key="sk-stub", anthropic_base_url=None,
        gemini_api_key="stub", planner_model="stub", critic_model="stub",
    )
    ctx = ToolContext(settings=settings, run_dir=out_dir,
                      layers_dir=layers_dir, run_id="smoke-subfig")
    # Pre-stage the parent so sub-panel registration can check collisions
    ctx.state["rendered_layers"]["ingest_fig_01"] = {
        "layer_id": "ingest_fig_01", "kind": "image", "name": "figure_1",
        "src_path": str(parent_path),
    }

    panels = [
        {"label": "a", "bbox": [0, 0, 300, 300],
         "caption": "Left panel (text input)", "short_caption": "Text"},
        {"label": "b", "bbox": [300, 0, 600, 300],
         "caption": "Right panel (image input)", "short_caption": "Image"},
    ]
    created = _register_sub_panels(
        parent_layer_id="ingest_fig_01",
        parent_path=parent_path,
        parent_caption="Composite — text + image inputs",
        panels=panels,
        ctx=ctx,
        pdf_path=Path("/fake/paper.pdf"),
        source_page=3,
    )

    if created != ["ingest_fig_01_a", "ingest_fig_01_b"]:
        _fail(f"sub-panel layer_ids wrong: {created}")

    for sub_id, expected_color, expected_caption in [
        ("ingest_fig_01_a", (200, 60, 60), "Text"),
        ("ingest_fig_01_b", (60, 100, 200), "Image"),
    ]:
        rec = ctx.state["rendered_layers"].get(sub_id)
        if rec is None:
            _fail(f"sub-panel {sub_id} not registered")
        if rec.get("parent_layer_id") != "ingest_fig_01":
            _fail(f"{sub_id} missing parent_layer_id breadcrumb")
        if rec.get("extract_strategy") != "sub_panel":
            _fail(f"{sub_id} extract_strategy wrong: {rec.get('extract_strategy')!r}")
        if rec.get("caption_short") != expected_caption:
            _fail(f"{sub_id} caption_short wrong: {rec.get('caption_short')!r}")
        crop_path = Path(rec["src_path"])
        if not crop_path.exists() or crop_path.stat().st_size == 0:
            _fail(f"{sub_id} crop file missing or empty: {crop_path}")
        with Image.open(crop_path) as im:
            w, h = im.size
            if (w, h) != (300, 300):
                _fail(f"{sub_id} crop dims wrong: {w}×{h}, expected 300×300")
            # Center pixel should match the panel color (parent split in half)
            px = im.getpixel((150, 150))
            # Drop alpha if present
            if isinstance(px, tuple) and len(px) >= 3:
                px = px[:3]
            if px != expected_color:
                _fail(f"{sub_id} center pixel {px} != expected {expected_color}")

    _ok(f"sub-panel registration: 2 panels cropped + registered "
        f"({len(created)} layers, parent breadcrumb intact, crops correct)")


def check_versioning_no_api() -> None:
    """v2.2 versioning: revise loops + edit_layer must NOT clobber prior
    intermediate state. Asserts:
      - render_text_layer writes layers/<id>.v<N>.png (not <id>.png)
      - edit_layer (re-render) bumps to v<N+1>; v<N> still on disk
      - 2 composite() calls produce composites/iter_01/ AND composites/iter_02/
      - final/ symlinks point at iter_02 (the latest)
      - tool_result.payload exposes relative_path / version / supersedes_*
    """
    print("[20/42] versioning + revise-loop preservation (no API)")
    from .config import REPO_ROOT, Settings
    from .tools import ToolContext
    from .tools.composite import composite
    from .tools.propose_design_spec import propose_design_spec
    from .tools.render_text_layer import render_text_layer
    from .tools.edit_layer import edit_layer
    from .tools.switch_artifact_type import switch_artifact_type

    out_dir = REPO_ROOT / "out" / "smoke_versioning"
    layers_dir = out_dir / "layers"
    if out_dir.exists():
        # Clean prior smoke run so version counters start at 0.
        import shutil
        shutil.rmtree(out_dir)
    layers_dir.mkdir(parents=True, exist_ok=True)

    settings = Settings(
        anthropic_api_key="sk-stub", anthropic_base_url=None,
        gemini_api_key="stub",
        planner_model="stub", critic_model="stub",
    )
    ctx = ToolContext(settings=settings, run_dir=out_dir,
                      layers_dir=layers_dir, run_id="smoke-vers")

    # Stub a background so composite has 2 layers
    bg_path = layers_dir / "bg_seed.png"
    Image.new("RGB", (768, 1024), (10, 10, 30)).save(bg_path)
    ctx.state["rendered_layers"]["L0_bg"] = {
        "layer_id": "L0_bg", "name": "bg", "kind": "background", "z_index": 0,
        "bbox": {"x": 0, "y": 0, "w": 768, "h": 1024},
        "src_path": str(bg_path), "prompt": "(stub)",
        "aspect_ratio": "3:4", "image_size": "1K",
        "safe_zones": [], "sha256": "seed",
    }

    switch_artifact_type({"type": "poster"}, ctx=ctx)
    propose_design_spec({"design_spec": {
        "brief": "v",
        "canvas": {"w_px": 768, "h_px": 1024, "dpi": 96,
                   "aspect_ratio": "3:4", "color_mode": "RGB"},
        "palette": ["#000", "#fff"],
        "typography": {}, "mood": [], "composition_notes": "",
        "layer_graph": [],
    }}, ctx=ctx)

    # ── v1 layer write ─────────────────────────────────────────────────
    r1 = render_text_layer({
        "layer_id": "L1", "name": "title", "text": "v1 text",
        "font_family": "NotoSansSC-Bold", "font_size_px": 60, "fill": "#fff",
        "bbox": {"x": 50, "y": 50, "w": 600, "h": 120},
        "align": "center", "z_index": 1,
    }, ctx=ctx)
    if r1.payload.get("version") != 1:
        _fail(f"first render should be v1; got version={r1.payload.get('version')}")
    if r1.payload.get("relative_path") != "layers/text_L1.v1.png":
        _fail(f"v1 relative_path wrong: {r1.payload.get('relative_path')}")
    if r1.payload.get("supersedes_sha256") is not None:
        _fail("first render should not have supersedes_sha256")
    v1_file = layers_dir / "text_L1.v1.png"
    if not v1_file.exists():
        _fail(f"v1 file missing: {v1_file}")
    v1_sha = r1.payload["sha256"]

    # ── v2 layer write (via edit_layer → re-render) ────────────────────
    r2 = edit_layer({"layer_id": "L1", "diff": {"text": "v2 text"}}, ctx=ctx)
    if r2.payload.get("version") != 2:
        _fail(f"edit_layer should bump to v2; got {r2.payload.get('version')}")
    if r2.payload.get("supersedes_sha256") != v1_sha:
        _fail(f"v2 supersedes_sha256 should equal v1 sha; got {r2.payload.get('supersedes_sha256')!r}")
    v2_file = layers_dir / "text_L1.v2.png"
    if not v2_file.exists():
        _fail(f"v2 file missing: {v2_file}")
    if not v1_file.exists():
        _fail(f"⚠ v1 file was clobbered (this is the bug we're guarding against): {v1_file}")
    _ok(f"layer versioning: v1 + v2 both on disk; v2.supersedes = v1.sha256[:8] {v1_sha[:8]}")

    # ── Composite iter 1 ──────────────────────────────────────────────
    c1 = composite({}, ctx=ctx)
    if c1.status != "ok":
        _fail(f"composite iter 1: {(c1.error_message or c1.payload)}")
    if c1.payload.get("iteration") != 1:
        _fail(f"composite iter 1 should report iteration=1; got {c1.payload.get('iteration')}")
    iter1_dir = out_dir / "composites" / "iter_01"
    for f in ("poster.html", "poster.psd", "poster.svg", "preview.png"):
        if not (iter1_dir / f).exists():
            _fail(f"iter_01/{f} missing")
    iter1_preview_sha = c1.payload["preview_sha256"]

    # ── Edit a layer + composite iter 2 ───────────────────────────────
    edit_layer({"layer_id": "L1", "diff": {"fill": "#ff00ff"}}, ctx=ctx)
    c2 = composite({}, ctx=ctx)
    if c2.status != "ok":
        _fail(f"composite iter 2: {(c2.error_message or c2.payload)}")
    if c2.payload.get("iteration") != 2:
        _fail(f"composite iter 2 should report iteration=2; got {c2.payload.get('iteration')}")
    iter2_dir = out_dir / "composites" / "iter_02"
    if not (iter2_dir / "preview.png").exists():
        _fail("iter_02/preview.png missing")
    if not (iter1_dir / "preview.png").exists():
        _fail("⚠ iter_01/preview.png was clobbered by iter_02 — versioning broken")
    if c2.payload.get("supersedes_preview_sha256") != iter1_preview_sha:
        _fail(f"iter_02 supersedes_preview_sha256 should be iter_01's preview sha")
    _ok(f"composite versioning: iter_01 + iter_02 both intact; "
        f"iter_02.supersedes_preview = iter_01.preview_sha[:8] {iter1_preview_sha[:8]}")

    # ── final/ symlinks point at the latest iter ──────────────────────
    final_dir = out_dir / "final"
    if not (final_dir / "preview.png").is_symlink():
        _fail("final/preview.png should be a symlink")
    if not (final_dir / "poster.html").is_symlink():
        _fail("final/poster.html should be a symlink")
    final_preview = (final_dir / "preview.png").resolve()
    if final_preview.parent.name != "iter_02":
        _fail(f"final/preview.png should resolve to iter_02; got {final_preview.parent.name}")
    _ok(f"final/ symlinks resolve to iter_02 (the latest)")


def _make_slide(layer_id: str, *, title: str, name: str | None = None,
                section_number: str | None = None,
                speaker_notes: str | None = None,
                role: str | None = None) -> LayerNode:
    """Compact LayerNode factory used by the v2.7.2 smokes — emits a
    minimal kind="slide" with a single title text child so section /
    notes assertions stay readable."""
    title_child = LayerNode(
        layer_id=f"{layer_id}_title", name="title", kind="text", z_index=10,
        bbox=SafeZone(x=120, y=80, w=1680, h=140),
        text=title, font_family="NotoSerifSC-Bold", font_size_px=72,
        align="left", effects=TextEffect(fill="#0f172a"),
    )
    return LayerNode(
        layer_id=layer_id,
        name=name or layer_id,
        kind="slide",
        z_index=1,
        role=role,  # type: ignore[arg-type]
        section_number=section_number,
        speaker_notes=speaker_notes,
        children=[title_child],
    )


def check_section_renumber_policy() -> None:
    """v2.7.2 smoke #25 — `apply_section_policy(policy="renumber")` walks
    a deck whose planner-supplied section_number is non-monotonic and
    rewrites the labels in slide order. Three content slides come back
    as §1 / §2 / §3 (no shared title prefix → no sub-rhythm)."""
    print("[25/42] section_number policy: renumber (no API)")
    from .util.section_renumber import apply_section_policy

    slides = [
        _make_slide("s1", title="Vision tokenizer", section_number="§3.1"),
        _make_slide("s2", title="Audio tokenizer", section_number="§2.2"),
        _make_slide("s3", title="Decoder fusion", section_number="§3.2"),
    ]
    out = apply_section_policy(slides, "renumber")

    if [s.section_number for s in slides] != ["§3.1", "§2.2", "§3.2"]:
        _fail("renumber policy mutated input slides — must be immutable")
    labels = [s.section_number for s in out]
    if labels != ["§1", "§2", "§3"]:
        _fail(f"expected ['§1', '§2', '§3'] after renumber; got {labels}")
    if [s.layer_id for s in out] != ["s1", "s2", "s3"]:
        _fail("renumber must preserve slide order + ids")
    _ok(f"renumber: {[s.section_number for s in slides]} -> {labels}")


def check_section_renumber_strip() -> None:
    """v2.7.2 smoke #26 — `apply_section_policy(policy="strip")` clears
    every SlideNode.section_number to None without touching titles or
    speaker notes."""
    print("[26/42] section_number policy: strip (no API)")
    from .util.section_renumber import apply_section_policy

    slides = [
        _make_slide("s1", title="A", section_number="§1",
                    speaker_notes="open"),
        _make_slide("s2", title="B", section_number="§2.1"),
        _make_slide("s3", title="C", section_number="§3"),
    ]
    out = apply_section_policy(slides, "strip")
    if any(s.section_number is not None for s in out):
        _fail("strip policy left a non-None section_number behind")
    if [s.section_number for s in slides] != ["§1", "§2.1", "§3"]:
        _fail("strip policy mutated input slides — must be immutable")
    if out[0].speaker_notes != "open":
        _fail("strip policy clobbered speaker_notes — must only touch section_number")
    _ok("strip: all section_number cleared, notes/titles intact")


def check_stable_id_notes_after_reorder() -> None:
    """v2.7.2 smoke #27 — speaker notes follow the SlideNode (by id),
    NOT the enumerate index. Build a 4-slide deck, reorder to
    [s4, s1, s3, s2], composite to .pptx, reopen and confirm each
    slide's notes match the source SlideNode it came from."""
    print("[27/42] speaker_notes follow slide_id after reorder (no API)")
    from pptx import Presentation as _Reopen

    from .config import REPO_ROOT, Settings
    from .schema import ArtifactType, DesignSpec
    from .tools import ToolContext
    from .tools.composite import composite
    from .tools.switch_artifact_type import switch_artifact_type

    out_dir = REPO_ROOT / "out" / "smoke_section_notes"
    layers_dir = out_dir / "layers"
    layers_dir.mkdir(parents=True, exist_ok=True)

    settings = Settings(
        anthropic_api_key="sk-stub", anthropic_base_url=None,
        gemini_api_key="stub", planner_model="stub", critic_model="stub",
    )
    ctx = ToolContext(settings=settings, run_dir=out_dir, layers_dir=layers_dir,
                      run_id="smoke-section-notes")

    if switch_artifact_type({"type": "deck"}, ctx=ctx).status != "ok":
        _fail("switch_artifact_type(deck)")

    s1 = _make_slide("s1", title="Tokenizers",
                     speaker_notes="NOTES-FOR-S1: tokenizer story")
    s2 = _make_slide("s2", title="Decoder",
                     speaker_notes="NOTES-FOR-S2: decoder story")
    s3 = _make_slide("s3", title="Results",
                     speaker_notes="NOTES-FOR-S3: results story")
    s4 = _make_slide("s4", title="Future Work",
                     speaker_notes="NOTES-FOR-S4: future story")

    reordered = [s4, s1, s3, s2]
    spec = DesignSpec(
        brief="Stable-id notes binding smoke",
        artifact_type=ArtifactType.DECK,
        canvas={"w_px": 1920, "h_px": 1080, "dpi": 96,
                "aspect_ratio": "16:9", "color_mode": "RGB"},
        layer_graph=reordered,
    )
    ctx.state["design_spec"] = spec

    obs = composite({}, ctx=ctx)
    if obs.status != "ok":
        _fail(f"composite(deck): {(obs.error_message or str(obs.payload))}")

    comp = ctx.state["composition"]
    if not comp.pptx_path or not Path(comp.pptx_path).exists():
        _fail(f"deck PPTX missing: {comp.pptx_path}")

    prs = _Reopen(comp.pptx_path)
    if len(prs.slides) != 4:
        _fail(f"expected 4 slides, got {len(prs.slides)}")

    expected_notes = [
        "NOTES-FOR-S4",  # s4 first
        "NOTES-FOR-S1",
        "NOTES-FOR-S3",
        "NOTES-FOR-S2",
    ]
    for idx, marker in enumerate(expected_notes):
        notes_text = prs.slides[idx].notes_slide.notes_text_frame.text
        if marker not in notes_text:
            _fail(
                f"slide {idx} notes mismatch: expected substring {marker!r}, "
                f"got {notes_text[:120]!r}. notes are following enumerate "
                f"index, not slide_id"
            )
    _ok("notes follow slide_id across reorder [s4, s1, s3, s2]")

    # The renumber policy is the default; verify each slide's title got
    # a section prefix in the rendered .pptx so we know the
    # apply_section_policy hook in _composite_deck actually fired.
    sections_seen = 0
    for idx in range(4):
        title_text = " ".join(
            run.text
            for shape in prs.slides[idx].shapes if shape.has_text_frame
            for para in shape.text_frame.paragraphs for run in para.runs
        )
        if "§" in title_text:
            sections_seen += 1
    if sections_seen == 0:
        _fail(
            "no slide title carries a section prefix — "
            "apply_section_policy did not fire in _composite_deck"
        )
    _ok(f"apply_section_policy fired in _composite_deck "
        f"({sections_seen}/4 slides carry section prefix)")


# ─────────────────────── v2.7.3 vision critic sub-agent ─────────────────────


class _ScriptedTurn:
    """One pre-baked turn the mock backend will replay. `tool_calls` is a list
    of `(tool_name, tool_input_dict)` tuples; the backend wraps each in a
    ToolCall with a synthetic id."""

    def __init__(self,
                 tool_calls: list[tuple[str, dict]] | None = None,
                 text: str = "",
                 stop_reason: str = "tool_use") -> None:
        self.tool_calls = tool_calls or []
        self.text = text
        self.stop_reason = stop_reason


class _MockCriticBackend:
    """Minimal LLMBackend stand-in for v2.7.3 sub-agent smokes.

    Replays a scripted list of turns. Each `create_turn` call records the
    incoming messages so the assertions can verify image bytes were threaded
    through, then returns the next scripted TurnResponse. Round-trip
    methods (`append_assistant`, `append_tool_results`) are no-ops since
    our scripted turns don't depend on prior context.
    """

    name = "mock"

    def __init__(self, model: str, turns: list[_ScriptedTurn]) -> None:
        self.model = model
        self._turns = list(turns)
        self.observed_messages: list[list] = []
        self._call_count = 0

    def create_turn(self, *, system, messages, tools, thinking_budget=0,
                    max_tokens=16384, extra_headers=None):
        from .llm_backend import ToolCall, TurnResponse
        # Snapshot the messages list so smokes can assert on what got
        # passed in (e.g. image bytes from prior tool_result turns).
        self.observed_messages.append(list(messages))
        if self._call_count >= len(self._turns):
            # No more scripted turns: emit a do-nothing end_turn so the
            # CriticAgent's max_turns failsafe triggers naturally.
            return TurnResponse(stop_reason="end_turn", raw_assistant_content={"role": "assistant"})
        scripted = self._turns[self._call_count]
        self._call_count += 1
        tcs = [
            ToolCall(id=f"toolu_mock_{i}", name=name, input=dict(args))
            for i, (name, args) in enumerate(scripted.tool_calls)
        ]
        return TurnResponse(
            text=scripted.text,
            tool_calls=tcs,
            stop_reason=scripted.stop_reason,
            usage={"input": 100, "output": 50, "cache_read": 0, "cache_create": 0},
            raw_assistant_content={"role": "assistant", "content": scripted.text},
        )

    def append_assistant(self, messages, response):
        messages.append(response.raw_assistant_content)

    def append_tool_results(self, messages, results):
        for tu_id, payload, _is_err in results:
            messages.append({"role": "tool", "tool_call_id": tu_id, "content": payload})

    def vision_user_message(self, *, image_b64, media_type, text):
        return {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{image_b64}"}},
            {"type": "text", "text": text},
        ]}


def _make_smoke_settings(out_root: Path) -> "Settings":  # noqa: F821
    """Build a Settings stub that points at a writable smoke run dir."""
    from .config import Settings
    return Settings(
        anthropic_api_key="sk-stub",
        anthropic_base_url=None,
        gemini_api_key="stub",
        planner_model="stub-planner",
        critic_model="stub-critic",
        out_dir=out_root,
        critic_max_turns=4,
        critic_thinking_budget=0,
    )


def _make_smoke_deck_spec(n_slides: int = 3) -> tuple["DesignSpec", list[Path], Path]:  # noqa: F821
    """Build a DesignSpec for a deck plus N rendered PNG files on disk under
    a temp dir (no real composite — we just need bytes for read_slide_render
    to base64-encode). Returns (spec, slide_paths, run_dir)."""
    from .config import REPO_ROOT
    from .schema import ArtifactType, DesignSpec, LayerNode

    run_dir = REPO_ROOT / "out" / "smoke_critic_subagent"
    if run_dir.exists():
        import shutil
        shutil.rmtree(run_dir)
    slides_dir = run_dir / "composites" / "iter_01" / "slides"
    slides_dir.mkdir(parents=True, exist_ok=True)

    slide_paths: list[Path] = []
    slide_nodes: list[LayerNode] = []
    for i in range(n_slides):
        png = slides_dir / f"slide_{i:02d}.png"
        Image.new("RGB", (640, 360), (200, 50 + i * 10, 80)).save(png)
        slide_paths.append(png)
        slide_nodes.append(LayerNode(
            layer_id=f"S{i}", name=f"slide_{i}", kind="slide",
            z_index=i, children=[],
        ))

    spec = DesignSpec(
        brief="smoke critic subagent",
        artifact_type=ArtifactType.DECK,
        canvas={"w_px": 1920, "h_px": 1080, "dpi": 96,
                "aspect_ratio": "16:9", "color_mode": "RGB"},
        layer_graph=slide_nodes,
    )
    return spec, slide_paths, run_dir


def check_critic_subagent_trajectory() -> None:
    """smoke #25: spawn CriticAgent on a fixture deck with a mocked
    LLMBackend that calls `report_verdict` on turn 1. Verify the resulting
    CritiqueReport has the expected verdict/score AND the trajectory file
    `critic.jsonl` lands in the run dir."""
    print("[28/42] critic sub-agent: scripted report_verdict + trajectory written")
    from .agents import CriticAgent
    from .schema import ArtifactType, CritiqueReport

    spec, slide_paths, run_dir = _make_smoke_deck_spec(n_slides=3)
    settings = _make_smoke_settings(run_dir.parent.parent)

    scripted = [
        _ScriptedTurn(tool_calls=[
            ("read_slide_render", {"slide_id": "S0"}),
        ]),
        _ScriptedTurn(tool_calls=[
            ("report_verdict", {
                "score": 0.82,
                "verdict": "pass",
                "summary": "structurally sound; minor typography drift only",
                "issues": [
                    {"slide_id": "S1", "severity": "low",
                     "category": "typography",
                     "description": "body font 22px below 24px lower bound",
                     "evidence_paper_anchor": None},
                ],
            }),
        ]),
    ]
    mock = _MockCriticBackend(model="stub-critic", turns=scripted)

    agent = CriticAgent(settings, ArtifactType.DECK)
    agent.backend = mock  # inject after construction (no API call needed)

    traj_path = run_dir / "trajectory" / "critic.jsonl"
    report = agent.critique(
        spec=spec, layer_manifest=[],
        slide_renders=slide_paths,
        paper_raw_text="LongCat-Next achieves 72.3% top-1 on ImageNet.",
        iteration=1, trajectory_path=traj_path,
    )

    if not isinstance(report, CritiqueReport):
        _fail(f"report should be CritiqueReport; got {type(report).__name__}")
    if report.verdict != "pass":
        _fail(f"verdict should be 'pass'; got {report.verdict!r}")
    if abs(report.score - 0.82) > 0.001:
        _fail(f"score not preserved: {report.score}")
    if len(report.issues) != 1 or report.issues[0].category != "typography":
        _fail(f"issues lost in roundtrip: {report.issues}")

    if not traj_path.exists():
        _fail(f"critic.jsonl not written at {traj_path}")
    lines = traj_path.read_text(encoding="utf-8").strip().splitlines()
    if len(lines) != 2:
        _fail(f"expected 2 trajectory lines (one per turn), got {len(lines)}")
    line0 = json.loads(lines[0])
    line1 = json.loads(lines[1])
    if line0.get("iteration") != 1 or line1.get("iteration") != 1:
        _fail(f"trajectory iteration field corrupted: {line0}, {line1}")
    if not any(tc.get("name") == "report_verdict" for tc in line1.get("tool_calls", [])):
        _fail(f"final turn should record report_verdict; got {line1.get('tool_calls')}")
    _ok(f"critic sub-agent: report_verdict captured + critic.jsonl ({len(lines)} lines)")


def check_critic_subagent_max_turns() -> None:
    """smoke #26: when the mocked LLM never calls `report_verdict`,
    CriticAgent must exhaust max_turns and synthesize a `verdict='fail'`
    CritiqueReport rather than recurse forever."""
    print("[29/42] critic sub-agent: max_turns failsafe → fail verdict")
    from .agents import CriticAgent
    from .schema import ArtifactType

    spec, slide_paths, run_dir = _make_smoke_deck_spec(n_slides=2)
    settings = _make_smoke_settings(run_dir.parent.parent)

    # Every scripted turn calls a non-terminal tool — never report_verdict.
    scripted = [
        _ScriptedTurn(tool_calls=[("read_slide_render", {"slide_id": "S0"})])
        for _ in range(settings.critic_max_turns + 4)
    ]
    mock = _MockCriticBackend(model="stub-critic", turns=scripted)

    agent = CriticAgent(settings, ArtifactType.DECK)
    agent.backend = mock

    traj_path = run_dir / "trajectory" / "critic.jsonl"
    if traj_path.exists():
        traj_path.unlink()
    report = agent.critique(
        spec=spec, layer_manifest=[],
        slide_renders=slide_paths, paper_raw_text=None,
        iteration=1, trajectory_path=traj_path,
    )

    if report.verdict != "fail":
        _fail(f"max_turns exhaustion should yield 'fail'; got {report.verdict!r}")
    if report.score != 0.0:
        _fail(f"failsafe report should have score=0.0; got {report.score}")
    if mock._call_count != settings.critic_max_turns:
        _fail(f"backend should be called critic_max_turns ({settings.critic_max_turns}) "
              f"times; got {mock._call_count}")
    if not report.issues or report.issues[0].severity != "blocker":
        _fail(f"failsafe report should carry a blocker issue: {report.issues}")
    _ok(f"max_turns ({settings.critic_max_turns}) hit → fail verdict synthesized "
        f"(no infinite loop; {mock._call_count} backend calls)")


def check_critic_planner_consumption() -> None:
    """smoke #27: the critique tool wraps CriticAgent and returns a
    CritiqueReport JSON in tool_result.payload. Verify that for each
    verdict (pass/revise/fail) the planner can route correctly:
      pass    → state.critique_results carries verdict='pass'
      revise  → state.critique_results carries verdict='revise'
      fail    → state.critique_results carries verdict='fail'
    """
    print("[30/42] planner consumption: pass/revise/fail routing")
    from .agents import CriticAgent
    from .config import REPO_ROOT
    from .schema import ArtifactType, CompositionArtifacts, DesignSpec, LayerNode
    from .tools import ToolContext
    from .tools.critique_tool import critique

    out_dir = REPO_ROOT / "out" / "smoke_critic_consume"
    if out_dir.exists():
        import shutil
        shutil.rmtree(out_dir)
    layers_dir = out_dir / "layers"
    slides_dir = out_dir / "composites" / "iter_01" / "slides"
    slides_dir.mkdir(parents=True, exist_ok=True)

    preview_path = slides_dir.parent / "preview.png"
    Image.new("RGB", (320, 180), (40, 40, 40)).save(preview_path)
    for i in range(2):
        Image.new("RGB", (320, 180), (90, 90, 90)).save(slides_dir / f"slide_{i:02d}.png")

    spec = DesignSpec(
        brief="planner consumption smoke",
        artifact_type=ArtifactType.DECK,
        canvas={"w_px": 1920, "h_px": 1080, "dpi": 96,
                "aspect_ratio": "16:9", "color_mode": "RGB"},
        layer_graph=[
            LayerNode(layer_id="S0", name="cover", kind="slide", z_index=0),
            LayerNode(layer_id="S1", name="content", kind="slide", z_index=1),
        ],
    )

    settings = _make_smoke_settings(out_dir.parent.parent)
    # Allow several critique invocations within a single fixture run.
    settings = settings.__class__(
        **{**settings.__dict__, "max_critique_iters": 5},
    )

    def _new_ctx() -> ToolContext:
        ctx = ToolContext(settings=settings, run_dir=out_dir,
                          layers_dir=layers_dir, run_id="smoke-critic-consume")
        ctx.state["design_spec"] = spec
        ctx.state["composition"] = CompositionArtifacts(
            preview_path=str(preview_path),
            layer_manifest=[
                {"layer_id": "S0", "name": "cover", "kind": "slide",
                 "index": 0, "children": []},
                {"layer_id": "S1", "name": "content", "kind": "slide",
                 "index": 1, "children": []},
            ],
        )
        return ctx

    def _run_with_verdict(verdict: str, score: float) -> dict:
        ctx = _new_ctx()
        scripted = [_ScriptedTurn(tool_calls=[(
            "report_verdict", {"score": score, "verdict": verdict,
                               "summary": f"smoke {verdict}", "issues": []},
        )])]
        mock = _MockCriticBackend(model="stub-critic", turns=scripted)

        # Patch CriticAgent so the inner make_backend call is bypassed.
        original_init = CriticAgent.__init__

        def _patched_init(self, settings_, artifact_type):
            self.settings = settings_
            self.artifact_type = artifact_type
            self.backend = mock
            self._system_prompt = "patched"

        CriticAgent.__init__ = _patched_init  # type: ignore[assignment]
        try:
            obs = critique({}, ctx=ctx)
        finally:
            CriticAgent.__init__ = original_init  # type: ignore[assignment]

        if obs.status != "ok":
            _fail(f"critique tool errored on verdict={verdict}: {obs.error_message}")
        crits = ctx.state["critique_results"]
        if not crits:
            _fail(f"critique_results empty after verdict={verdict}")
        if crits[-1].verdict != verdict:
            _fail(f"verdict roundtrip {verdict} → {crits[-1].verdict}")
        return obs.payload

    pass_payload = _run_with_verdict("pass", 0.85)
    revise_payload = _run_with_verdict("revise", 0.62)
    fail_payload = _run_with_verdict("fail", 0.30)

    for label, p in (("pass", pass_payload), ("revise", revise_payload),
                     ("fail", fail_payload)):
        if p.get("verdict") != label:
            _fail(f"tool_result.payload.verdict {label} mismatch: {p}")
        if "score" not in p or "summary" not in p:
            _fail(f"tool_result.payload missing fields for {label}: {sorted(p.keys())}")
    _ok("CritiqueReport JSON pass/revise/fail all surface in tool_result.payload "
        "with verdict + score + summary intact")


def check_critic_subagent_png_throughput() -> None:
    """smoke #28: long-tail check that CriticAgent + read_slide_render
    survive a 15-slide deck with realistic file sizes (no OOM, all renders
    accessible by slide_id, sub-agent terminates cleanly)."""
    print("[31/42] critic sub-agent: 15-slide PNG throughput (no OOM)")
    from .agents import CriticAgent
    from .schema import ArtifactType

    spec, slide_paths, run_dir = _make_smoke_deck_spec(n_slides=15)
    # Re-write each slide PNG at a more realistic resolution so the base64
    # path actually exercises non-trivial bytes — 1920x1080 is the deck
    # canvas default. Keeps the encode under 200KB per slide post-jpeg.
    for i, p in enumerate(slide_paths):
        Image.new("RGB", (1920, 1080), (30 + i * 5, 60, 90)).save(p)

    settings = _make_smoke_settings(run_dir.parent.parent)

    # Script: read every slide once, then report_verdict.
    read_calls = [
        _ScriptedTurn(tool_calls=[
            ("read_slide_render", {"slide_id": f"S{i}"}),
        ])
        for i in range(15)
    ]
    final_call = _ScriptedTurn(tool_calls=[(
        "report_verdict",
        {"score": 0.78, "verdict": "pass", "summary": "all 15 slides reviewed",
         "issues": []},
    )])
    # Because critic_max_turns defaults to 4 in the smoke settings, raise
    # it for this stress test so the agent reaches the terminal call.
    big_settings = settings.__class__(
        **{**settings.__dict__, "critic_max_turns": 20,
           "critic_preview_max_edge": 1024},
    )
    mock = _MockCriticBackend(model="stub-critic", turns=read_calls + [final_call])

    agent = CriticAgent(big_settings, ArtifactType.DECK)
    agent.backend = mock

    traj_path = run_dir / "trajectory" / "critic_long.jsonl"
    if traj_path.exists():
        traj_path.unlink()
    report = agent.critique(
        spec=spec, layer_manifest=[], slide_renders=slide_paths,
        paper_raw_text=None, iteration=1, trajectory_path=traj_path,
    )

    if report.verdict != "pass" or len(report.issues) != 0:
        _fail(f"15-slide run should pass cleanly; got verdict={report.verdict} "
              f"issues={len(report.issues)}")
    if mock._call_count != 16:
        _fail(f"expected 16 backend calls (15 reads + 1 verdict); got {mock._call_count}")

    # Spot-check that the first read_slide_render result actually carried
    # base64 PNG bytes (not a stub). The mock records observed_messages —
    # the second create_turn call sees the tool result for slide S0.
    if len(mock.observed_messages) < 2:
        _fail("mock should have observed at least 2 turns of messages")
    second_turn_msgs = mock.observed_messages[1]
    found_b64 = False
    for m in second_turn_msgs:
        # The OpenAI-style tool result is `{"role": "tool", "content": "..."}`
        # carrying the JSON-serialized payload from the critic dispatch.
        if isinstance(m, dict) and m.get("role") == "tool":
            content = m.get("content") or ""
            if "image_b64" in content and len(content) > 1000:
                found_b64 = True
                break
    if not found_b64:
        _fail("second turn's messages should carry base64 PNG payload from "
              "first read_slide_render call")
    _ok(f"15-slide PNG throughput: {mock._call_count} backend calls, "
        f"base64 payloads threaded through, verdict={report.verdict}")


# ─────────────────────── v2.8.1 archetype Phase 1 ──────────────────────


def _make_archetype_slide(
    *,
    layer_id: str,
    archetype: str,
    children: list[LayerNode],
    section_number: str | None = None,
    speaker_notes: str | None = None,
    name: str | None = None,
) -> LayerNode:
    """LayerNode factory for archetype smokes — emits a kind="slide"
    with the given children and the specified archetype label."""
    return LayerNode(
        layer_id=layer_id,
        name=name or layer_id,
        kind="slide",
        z_index=1,
        archetype=archetype,  # type: ignore[arg-type]
        section_number=section_number,
        speaker_notes=speaker_notes,
        children=children,
    )


def _new_blank_slide(slide_w: int = 1920, slide_h: int = 1080) -> tuple:
    """Spin up a fresh blank Presentation + add one blank slide.

    Returns (Presentation, Slide). Used by every archetype smoke so
    each runs in isolation."""
    from pptx import Presentation as _Pres
    from pptx.util import Emu as _Emu
    prs = _Pres()
    prs.slide_width = _Emu(slide_w * 9525)
    prs.slide_height = _Emu(slide_h * 9525)
    blank = prs.slide_layouts[6]
    slide = prs.slides.add_slide(blank)
    return prs, slide


def _make_archetype_ctx() -> "ToolContext":  # noqa: F821
    """Lightweight ToolContext stub for archetype smokes — no API
    keys, no real run dir; archetype renderers don't touch ctx beyond
    settings.fonts on the preview path (which we don't exercise here)."""
    from .config import REPO_ROOT, Settings
    from .tools import ToolContext

    settings = Settings(
        anthropic_api_key="sk-stub", anthropic_base_url=None,
        gemini_api_key="stub",
        planner_model="stub", critic_model="stub",
    )
    out_dir = REPO_ROOT / "out" / "smoke_archetype"
    out_dir.mkdir(parents=True, exist_ok=True)
    layers_dir = out_dir / "layers"
    layers_dir.mkdir(parents=True, exist_ok=True)
    return ToolContext(
        settings=settings, run_dir=out_dir,
        layers_dir=layers_dir, run_id="smoke-archetype",
    )


def _all_textframes(slide: "Any") -> list:  # noqa: F821
    """Return the slide's shapes that carry a text_frame (covers
    textboxes, autoshapes, and placeholders)."""
    return [s for s in slide.shapes if s.has_text_frame]


def _shape_text(shape: "Any") -> str:  # noqa: F821
    return shape.text_frame.text or ""


def _shape_max_pt(shape: "Any") -> int:  # noqa: F821
    """Largest run font size (in pt) across the shape's text frame.
    Returns 0 when no run sets an explicit size."""
    best = 0
    for para in shape.text_frame.paragraphs:
        for run in para.runs:
            sz = getattr(run.font, "size", None)
            if sz is not None:
                # `sz` is an Emu-like length subclass with `.pt` attr.
                try:
                    best = max(best, int(sz.pt))
                except Exception:
                    pass
    return best


def check_archetype_cover_editorial() -> None:
    """v2.8.1 smoke #37 — `cover_editorial` renders ≥3 native shapes
    (title + subtitle + author strip), all backed by TextFrames. When
    the slide carries a `section_number`, the title text picks up the
    `§N · ` prefix from v2.7.2's `_with_section_prefix`."""
    print("[37/42] archetype cover_editorial: ≥3 textframes + section prefix")
    from .tools.pptx_renderer import _render_slide

    title = LayerNode(
        layer_id="c1_title", name="title", kind="text", z_index=10,
        text="OpenDesign", font_family="NotoSerifSC-Bold", font_size_px=120,
        align="left", effects=TextEffect(fill="#0f172a"),
    )
    subtitle = LayerNode(
        layer_id="c1_sub", name="subtitle", kind="text", z_index=10,
        text="A conversational design agent", font_family="NotoSansSC-Bold",
        font_size_px=36, align="left", effects=TextEffect(fill="#475569"),
    )
    authors = LayerNode(
        layer_id="c1_authors", name="author_byline", kind="text", z_index=10,
        text="Yaxin Luo · Anthropic Skill Team", font_family="NotoSansSC-Bold",
        font_size_px=26, align="left", effects=TextEffect(fill="#64748b"),
    )
    slide_node = _make_archetype_slide(
        layer_id="cover", archetype="cover_editorial",
        section_number="§1",
        children=[title, subtitle, authors],
    )
    _, slide = _new_blank_slide()
    _render_slide(slide, slide_node, 1920, 1080, _make_archetype_ctx())

    frames = _all_textframes(slide)
    if len(frames) < 3:
        _fail(f"cover_editorial should emit ≥3 TextFrames; got {len(frames)}")

    texts = [_shape_text(f) for f in frames]
    title_match = [t for t in texts if "OpenDesign" in t]
    if not title_match:
        _fail(f"cover_editorial title not rendered; texts={texts}")
    if not title_match[0].startswith("§1"):
        _fail(f"section_number prefix missing; title={title_match[0]!r}")
    if not any("conversational" in t for t in texts):
        _fail(f"subtitle missing from cover; texts={texts}")
    if not any("Yaxin Luo" in t for t in texts):
        _fail(f"authors strip missing from cover; texts={texts}")
    _ok(f"cover_editorial: {len(frames)} textframes, "
        f"section prefix '§1' on title, subtitle + authors present")


def check_archetype_evidence_snapshot() -> None:
    """v2.8.1 smoke #38 — `evidence_snapshot` renders one giant number
    (≥200 pt source ⇒ ≥150 pt rendered) plus a footnote textframe."""
    print("[38/42] archetype evidence_snapshot: huge number + footnote")
    from .tools.pptx_renderer import _render_slide

    big = LayerNode(
        layer_id="e1_big", name="big_number", kind="text", z_index=10,
        text="72.3%", font_family="NotoSerifSC-Bold", font_size_px=240,
        align="center", effects=TextEffect(fill="#0f172a"),
    )
    foot = LayerNode(
        layer_id="e1_foot", name="stat_caption", kind="text", z_index=10,
        text="ImageNet top-1, single-crop", font_family="NotoSansSC-Bold",
        font_size_px=28, align="center", effects=TextEffect(fill="#475569"),
    )
    slide_node = _make_archetype_slide(
        layer_id="ev1", archetype="evidence_snapshot",
        children=[big, foot],
    )
    _, slide = _new_blank_slide()
    _render_slide(slide, slide_node, 1920, 1080, _make_archetype_ctx())

    frames = _all_textframes(slide)
    if len(frames) < 2:
        _fail(f"evidence_snapshot should emit ≥2 TextFrames; got {len(frames)}")

    big_frames = [f for f in frames if "72.3%" in _shape_text(f)]
    if not big_frames:
        _fail(f"big number frame missing; texts={[_shape_text(f) for f in frames]}")
    big_pt = _shape_max_pt(big_frames[0])
    # 240 px source × 0.75 px-to-pt ≈ 180 pt; threshold 150 leaves
    # room for any minor calibration drift.
    if big_pt < 150:
        _fail(f"big number font too small: {big_pt} pt; want ≥150")
    if not any("ImageNet" in _shape_text(f) for f in frames):
        _fail(f"footnote missing; texts={[_shape_text(f) for f in frames]}")
    _ok(f"evidence_snapshot: number frame at {big_pt} pt, footnote present")


def check_archetype_takeaway_list() -> None:
    """v2.8.1 smoke #39 — `takeaway_list` renders 3 bullet groups
    (marker + body shapes per row), giving ≥6 textframes plus the
    title."""
    print("[39/42] archetype takeaway_list: 3 bullet groups")
    from .tools.pptx_renderer import _render_slide

    title = LayerNode(
        layer_id="t1_title", name="title", kind="text", z_index=10,
        text="Three things to remember", font_family="NotoSerifSC-Bold",
        font_size_px=72, align="left", effects=TextEffect(fill="#0f172a"),
    )
    bullets = [
        LayerNode(
            layer_id=f"t1_b{i}", name=f"bullet_{i}", kind="text", z_index=10,
            text=text, font_family="NotoSansSC-Bold", font_size_px=36,
            align="left", effects=TextEffect(fill="#0f172a"),
        )
        for i, text in enumerate([
            "Editability beats prettier raster output.",
            "Provenance is a hard gate, not a soft check.",
            "Archetypes give the planner a vocabulary.",
        ], start=1)
    ]
    slide_node = _make_archetype_slide(
        layer_id="tk1", archetype="takeaway_list",
        children=[title, *bullets],
    )
    _, slide = _new_blank_slide()
    _render_slide(slide, slide_node, 1920, 1080, _make_archetype_ctx())

    frames = _all_textframes(slide)
    # 1 title + 3 markers + 3 bodies = 7 minimum
    if len(frames) < 7:
        _fail(f"takeaway_list should emit ≥7 TextFrames (title+3 markers+3 bodies); "
              f"got {len(frames)}")

    body_texts = [_shape_text(f) for f in frames]
    for marker in ("01", "02", "03"):
        if not any(marker == t.strip() for t in body_texts):
            _fail(f"takeaway marker '{marker}' missing; texts={body_texts}")
    for needle in ("Editability", "Provenance", "Archetypes"):
        if not any(needle in t for t in body_texts):
            _fail(f"takeaway body missing '{needle}'; texts={body_texts}")
    _ok(f"takeaway_list: {len(frames)} textframes, all 3 bullet groups present")


def check_archetype_thanks_qa() -> None:
    """v2.8.1 smoke #40 — `thanks_qa` renders a thanks/Q&A headline,
    a contact row, and an optional code link."""
    print("[40/42] archetype thanks_qa: headline + contact row")
    from .tools.pptx_renderer import _render_slide

    title = LayerNode(
        layer_id="q1_title", name="title", kind="text", z_index=10,
        text="Thanks · Questions?", font_family="NotoSerifSC-Bold",
        font_size_px=96, align="center", effects=TextEffect(fill="#0f172a"),
    )
    contact = LayerNode(
        layer_id="q1_email", name="contact_email", kind="text", z_index=10,
        text="luoyaxin999@gmail.com", font_family="NotoSansSC-Bold",
        font_size_px=28, align="center", effects=TextEffect(fill="#475569"),
    )
    code = LayerNode(
        layer_id="q1_link", name="code_link", kind="text", z_index=10,
        text="github.com/yaxin/open-design", font_family="JetBrainsMono",
        font_size_px=24, align="center", effects=TextEffect(fill="#7f1d1d"),
    )
    slide_node = _make_archetype_slide(
        layer_id="ty", archetype="thanks_qa",
        children=[title, contact, code],
    )
    _, slide = _new_blank_slide()
    _render_slide(slide, slide_node, 1920, 1080, _make_archetype_ctx())

    frames = _all_textframes(slide)
    if len(frames) < 3:
        _fail(f"thanks_qa should emit ≥3 TextFrames; got {len(frames)}")
    texts = [_shape_text(f) for f in frames]
    if not any("Thanks" in t and "Questions" in t for t in texts):
        _fail(f"thanks_qa headline missing; texts={texts}")
    if not any("luoyaxin999" in t for t in texts):
        _fail(f"thanks_qa contact missing; texts={texts}")
    if not any("github.com" in t for t in texts):
        _fail(f"thanks_qa code link missing; texts={texts}")
    _ok(f"thanks_qa: headline + contact + link rendered ({len(frames)} frames)")


def check_archetype_fallback_default() -> None:
    """v2.8.1 smoke #41 — when `archetype` is the schema default
    (`"evidence_snapshot"`) and the slide has no big-number child,
    the dispatcher falls through to the original default render. Same
    for any Phase 2/3 placeholder. No exceptions; output matches the
    pre-v2.8.1 inline path."""
    print("[41/42] archetype fallback: default + Phase 2/3 placeholder")
    from .tools.pptx_renderer import _render_slide

    # Case A: default archetype, ordinary slide (no big number, no
    # bullets). Should fall through to default render without errors.
    title = LayerNode(
        layer_id="d1_title", name="title", kind="text", z_index=10,
        text="An ordinary slide", font_family="NotoSerifSC-Bold",
        font_size_px=72, align="left", effects=TextEffect(fill="#0f172a"),
    )
    body = LayerNode(
        layer_id="d1_body", name="body", kind="text", z_index=10,
        text="Two paragraph body that should render through the\n"
             "v2.7.2 default path because no big number is declared.",
        font_family="NotoSansSC-Bold", font_size_px=32,
        align="left", effects=TextEffect(fill="#0f172a"),
    )
    slide_default = LayerNode(
        layer_id="ds1", name="ds1", kind="slide", z_index=1,
        section_number="§2",  # exercise v2.7.2 prefix on default path
        children=[title, body],
        # archetype omitted → schema default "evidence_snapshot"
    )
    _, slide_a = _new_blank_slide()
    _render_slide(slide_a, slide_default, 1920, 1080, _make_archetype_ctx())
    frames_a = _all_textframes(slide_a)
    if not frames_a:
        _fail("default-archetype fallback emitted no shapes")
    if not any("§2" in _shape_text(f) for f in frames_a):
        _fail("v2.7.2 section prefix missing on default-render fallback path")

    # Case B: Phase 2/3 placeholder archetype. Dispatcher should fall
    # through to default render without raising.
    slide_placeholder = _make_archetype_slide(
        layer_id="ds2", archetype="pipeline_horizontal",
        children=[title, body],
    )
    _, slide_b = _new_blank_slide()
    _render_slide(slide_b, slide_placeholder, 1920, 1080, _make_archetype_ctx())
    frames_b = _all_textframes(slide_b)
    if not frames_b:
        _fail("Phase 2 placeholder fallback emitted no shapes")
    _ok(f"fallback paths intact: default ({len(frames_a)} frames) + "
        f"placeholder pipeline_horizontal ({len(frames_b)} frames)")


def check_archetype_determinism() -> None:
    """v2.8.1 smoke #42 — rendering the same SlideNode twice produces
    byte-identical slide XML. Guards against accidental nondeterminism
    (time-based ids, random shape positions) creeping into archetype
    renderers."""
    print("[42/42] archetype determinism: two renders → identical XML")
    from xml.etree import ElementTree as ET
    from .tools.pptx_renderer import _render_slide

    title = LayerNode(
        layer_id="d_title", name="title", kind="text", z_index=10,
        text="Deterministic cover", font_family="NotoSerifSC-Bold",
        font_size_px=120, align="left", effects=TextEffect(fill="#0f172a"),
    )
    subtitle = LayerNode(
        layer_id="d_sub", name="subtitle", kind="text", z_index=10,
        text="Same input → same shapes", font_family="NotoSansSC-Bold",
        font_size_px=36, align="left", effects=TextEffect(fill="#475569"),
    )
    authors = LayerNode(
        layer_id="d_auth", name="author_byline", kind="text", z_index=10,
        text="OpenDesign", font_family="NotoSansSC-Bold",
        font_size_px=26, align="left", effects=TextEffect(fill="#64748b"),
    )
    spec_node = _make_archetype_slide(
        layer_id="dcov", archetype="cover_editorial",
        section_number="§1",
        children=[title, subtitle, authors],
    )

    _, slide_1 = _new_blank_slide()
    _render_slide(slide_1, spec_node, 1920, 1080, _make_archetype_ctx())
    _, slide_2 = _new_blank_slide()
    _render_slide(slide_2, spec_node, 1920, 1080, _make_archetype_ctx())

    xml_1 = ET.tostring(slide_1._element, encoding="unicode")
    xml_2 = ET.tostring(slide_2._element, encoding="unicode")

    if xml_1 != xml_2:
        # Surface a small diff hint for triage.
        for i, (a, b) in enumerate(zip(xml_1, xml_2)):
            if a != b:
                _fail(
                    f"determinism violated at char {i}: "
                    f"...{xml_1[max(0,i-30):i+30]!r} vs "
                    f"...{xml_2[max(0,i-30):i+30]!r}"
                )
        _fail("determinism violated (lengths differ)")
    _ok(f"determinism: byte-identical slide XML across two renders "
        f"({len(xml_1)} chars)")


def main() -> int:
    check_imports()
    check_tool_registry()
    check_pydantic_roundtrip()
    check_fonts()
    check_composite_no_api()
    check_svg_text_is_vector()
    check_chat_session_roundtrip()
    check_edit_layer_no_api()
    check_apply_edits_roundtrip()
    check_landing_mode()
    check_design_system_styles()
    check_landing_with_images()
    check_deck_mode()
    check_reasoning_step_roundtrip()
    check_ingest_document_markdown()
    check_ingest_document_image()
    check_ingest_document_docx()
    check_ingest_document_pptx()
    check_sub_figure_registration()
    check_versioning_no_api()
    check_deck_design_system_template()
    check_footer_leakage()
    check_callout_overlay()
    check_provenance_validator()
    check_section_renumber_policy()
    check_section_renumber_strip()
    check_stable_id_notes_after_reorder()
    check_critic_subagent_trajectory()
    check_critic_subagent_max_turns()
    check_critic_planner_consumption()
    check_critic_subagent_png_throughput()
    check_archetype_cover_editorial()
    check_archetype_evidence_snapshot()
    check_archetype_takeaway_list()
    check_archetype_thanks_qa()
    check_archetype_fallback_default()
    check_archetype_determinism()
    print("\n  smoke test passed.")
    print("  artifacts in: out/smoke/, out/smoke_edit/, out/smoke_apply/, "
          "out/smoke_landing/, out/smoke_styles/, out/smoke_landing_img/, "
          "out/smoke_deck/, out/smoke_ingest_md/, out/smoke_ingest_image/, "
          "out/smoke_ingest_docx/, out/smoke_ingest_pptx/, out/smoke_sub_figs/, "
          "out/smoke_section_notes/, "
          "out/smoke_critic_subagent/, out/smoke_critic_consume/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
