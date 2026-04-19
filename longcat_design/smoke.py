"""No-API smoke test: imports + schemas + fonts + a real composite call.

Run with:
    python -m longcat_design.smoke

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
    LayerNode, SafeZone, TextEffect, ToolObservation, Trajectory,
)


def _fail(msg: str) -> None:
    print(f"  FAIL  {msg}")
    sys.exit(1)


def _ok(msg: str) -> None:
    print(f"  ok    {msg}")


def check_imports() -> None:
    print("[1/8] imports")
    from . import chat, cli, config, critic, planner, runner, schema, session  # noqa
    from .tools import (
        TOOL_HANDLERS, TOOL_SCHEMAS, ToolContext,
        composite, critique_tool, edit_layer, fetch_brand_asset, finalize,
        generate_background, propose_design_spec, render_text_layer,
        switch_artifact_type,
    )  # noqa
    from .util import ids, io, logging  # noqa
    _ok("all modules import (incl. chat + session + edit_layer)")


def check_tool_registry() -> None:
    print("[2/8] tool registry")
    from .tools import TOOL_HANDLERS, TOOL_SCHEMAS

    expected = {"switch_artifact_type", "propose_design_spec",
                "generate_background", "render_text_layer", "edit_layer",
                "fetch_brand_asset", "composite", "critique", "finalize"}
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
    print("[3/8] pydantic schema round-trip")
    spec = DesignSpec(
        brief="国宝回家 公益项目主视觉海报，竖版 3:4",
        canvas={"w_px": 1536, "h_px": 2048, "dpi": 300, "aspect_ratio": "3:4", "color_mode": "RGB"},
        palette=["#1a0f0a", "#fafafa", "#a02018"],
        typography={"title_font": "NotoSerifSC-Bold", "subtitle_font": "NotoSansSC-Bold"},
        mood=["oriental epic"],
        composition_notes="hero centered",
        layer_graph=[
            LayerNode(layer_id="L0", name="background", kind="background", z_index=0,
                      bbox=SafeZone(x=0, y=0, w=1536, h=2048)),
            LayerNode(layer_id="L1", name="title", kind="text", z_index=1,
                      bbox=SafeZone(x=96, y=120, w=1344, h=320, purpose="title"),
                      text="国宝回家", font_family="NotoSerifSC-Bold", font_size_px=220,
                      align="center",
                      effects=TextEffect(fill="#fafafa",
                                         shadow={"color": "#00000080", "dx": 0, "dy": 6, "blur": 18})),
        ],
    )
    obs = ToolObservation(status="ok", summary="hi", artifacts=["a.png"])
    trace = [AgentTraceStep(step_idx=1, timestamp=datetime.now(),
                            actor="user", type="input", text="brief")]
    crit = CritiqueResult(iteration=1, verdict="pass", score=0.82,
                          issues=[], rationale="looks good")
    comp = CompositionArtifacts(psd_path="x.psd", svg_path="x.svg", preview_path="x.png",
                                layer_manifest=[])
    traj = Trajectory(
        run_id="smoke", created_at=datetime.now(),
        brief=spec.brief, design_spec=spec, layer_graph=spec.layer_graph,
        agent_trace=trace, critique_loop=[crit], composition=comp,
        metadata={"version": "v0"},
    )
    dumped = traj.model_dump(mode="json")
    _ = json.dumps(dumped, ensure_ascii=False)
    try:
        Trajectory.model_validate(dumped)
    except ValidationError as e:
        _fail(f"Trajectory round-trip: {e.errors()[:3]}")
    _ok(f"Trajectory round-trips ({len(json.dumps(dumped))} bytes)")
    _ = obs.model_dump()


def check_fonts() -> None:
    print("[4/8] fonts")
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
    print("[5/8] composite (no API)")
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
        _fail(f"switch_artifact_type: {obs.summary}")
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
        _fail(f"propose_design_spec: {obs.summary}")

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
    if obs.status not in ("ok", "partial"):
        _fail(f"render_text_layer title: {obs.summary}")

    obs = render_text_layer({
        "layer_id": "L2_subtitle", "name": "subtitle",
        "text": "National Treasures Return Home",
        "font_family": "NotoSansSC-Bold", "font_size_px": 36, "fill": "#c9a45a",
        "bbox": {"x": 64, "y": 280, "w": 640, "h": 60}, "align": "center",
        "z_index": 2,
    }, ctx=ctx)
    if obs.status not in ("ok", "partial"):
        _fail(f"render_text_layer subtitle: {obs.summary}")

    obs = composite({}, ctx=ctx)
    if obs.status != "ok":
        _fail(f"composite: {obs.summary}")

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
    print("[6/8] SVG + HTML content (vector text, contenteditable, inline fonts)")
    from .config import REPO_ROOT
    out_dir = REPO_ROOT / "out" / "smoke"

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
        "generator meta":       '<meta name="generator" content="LongcatDesign"',
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
        "apply-edits hint":     "longcat-design apply-edits",
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
    print("[7/8] chat session save/load")
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
    print("[8/8] edit_layer (no API)")
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
    if obs.status not in ("ok", "partial"):
        _fail(f"seed: render_text_layer: {obs.summary}")

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
    if obs.status not in ("ok", "partial"):
        _fail(f"edit_layer happy path: status={obs.status} summary={obs.summary}")

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
    if obs.status not in ("ok", "partial"):
        _fail(f"bbox partial merge: {obs.summary}")
    bbox = ctx.state["rendered_layers"]["L1_title"]["bbox"]
    if not (bbox["x"] == 48 and bbox["y"] == 200 and bbox["w"] == 672 and bbox["h"] == 180):
        _fail(f"bbox partial merge broken: {bbox}")
    _ok("partial bbox merge: y updated, x/w/h preserved")

    # --- Missing layer_id --------------------------------------------------
    obs = edit_layer({"layer_id": "nope", "diff": {"text": "x"}}, ctx=ctx)
    if obs.status != "not_found":
        _fail(f"unknown layer should return not_found, got {obs.status}")
    _ok("unknown layer_id → not_found")

    # --- Non-text layer rejected ------------------------------------------
    obs = edit_layer({"layer_id": "L0_bg", "diff": {"text": "x"}}, ctx=ctx)
    if obs.status != "error":
        _fail(f"bg layer edit should error, got {obs.status}")
    if "generate_background" not in obs.summary:
        _fail(f"bg-error summary should mention generate_background; got: {obs.summary}")
    _ok("non-text layer → error with redirect to generate_background")

    # --- Empty / unknown diff fields --------------------------------------
    if edit_layer({"layer_id": "L1_title", "diff": {}}, ctx=ctx).status != "error":
        _fail("empty diff should error")
    if edit_layer({"layer_id": "L1_title",
                   "diff": {"color": "#ff0000"}}, ctx=ctx).status != "error":
        _fail("unknown diff field should error (caught 'color' instead of 'fill')")
    _ok("empty diff + unknown field both rejected")


def main() -> int:
    check_imports()
    check_tool_registry()
    check_pydantic_roundtrip()
    check_fonts()
    check_composite_no_api()
    check_svg_text_is_vector()
    check_chat_session_roundtrip()
    check_edit_layer_no_api()
    print("\n  smoke test passed.")
    print("  artifacts in: out/smoke/, out/smoke_edit/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
