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
    LayerNode, SafeZone, TextEffect, ThinkingBlockRecord, ToolObservation,
    Trajectory,
)


def _fail(msg: str) -> None:
    print(f"  FAIL  {msg}")
    sys.exit(1)


def _ok(msg: str) -> None:
    print(f"  ok    {msg}")


def check_imports() -> None:
    print("[1/16] imports")
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
    print("[2/16] tool registry")
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
    print("[3/16] pydantic schema round-trip")
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
    print("[4/16] fonts")
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
    print("[5/16] composite (no API)")
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
    print("[6/16] SVG + HTML content (vector text, contenteditable, inline fonts)")
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
    print("[7/16] chat session save/load")
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
    print("[8/16] edit_layer (no API)")
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


def check_apply_edits_roundtrip() -> None:
    """HTML → apply-edits → new PSD/SVG/HTML/preview with same semantic content."""
    print("[9/16] apply-edits round-trip (no API)")
    from .apply_edits import apply_edits
    from .config import REPO_ROOT, Settings

    src_html = REPO_ROOT / "out" / "smoke" / "poster.html"
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

    traj, traj_path = apply_edits(edited_html_path, settings=settings,
                                  out_dir=out_dir)

    # --- trajectory assertions -------------------------------------------
    if traj.metadata.get("source") != "apply-edits":
        _fail(f"metadata.source != 'apply-edits'; got {traj.metadata.get('source')!r}")
    if not traj.metadata.get("parent_run_id"):
        _fail("metadata.parent_run_id missing — <meta name='ld-run-id'> may not be emitted")
    if traj.agent_trace:
        _fail(f"apply-edits trajectory should have empty agent_trace; got {len(traj.agent_trace)}")
    _ok(f"trajectory: source=apply-edits, parent={traj.metadata['parent_run_id']}, "
        f"{len(traj.layer_graph)} layers")

    # --- artifact files exist --------------------------------------------
    for label, p in [("PSD", traj.composition.psd_path),
                     ("SVG", traj.composition.svg_path),
                     ("HTML", traj.composition.html_path),
                     ("preview", traj.composition.preview_path)]:
        if p is None or not Path(p).exists() or Path(p).stat().st_size == 0:
            _fail(f"{label} not written: {p}")
    _ok("PSD+SVG+HTML+preview all regenerated in new run_dir")

    # --- edits landed in re-rendered layer graph -------------------------
    title = next((L for L in traj.layer_graph
                  if L.kind == "text" and L.name == "title"), None)
    if title is None:
        _fail("title layer missing from round-trip layer_graph")
    if title.font_size_px != 140:
        _fail(f"edit lost on round-trip — title.font_size_px={title.font_size_px}, expected 140")
    if (title.effects and title.effects.fill.lower()) != "#ff3366":
        _fail(f"edit lost on round-trip — title.effects.fill="
              f"{title.effects.fill if title.effects else None}, expected '#ff3366'")
    _ok("edits preserved: title.font_size_px=140, fill=#ff3366")

    # --- bg was decoded from data: URI, not copied from original ---------
    bg = next((L for L in traj.layer_graph if L.kind == "background"), None)
    if bg is not None:
        bg_path = Path(bg.src_path)
        if not bg_path.exists():
            _fail(f"bg src_path missing: {bg_path}")
        if out_dir not in bg_path.parents:
            _fail(f"bg was not written into new run_dir — got {bg_path}")
        _ok(f"bg decoded from data URI → {bg_path.name} ({bg_path.stat().st_size} B)")


def check_landing_mode() -> None:
    """Landing end-to-end: section-tree spec → HTML + preview → apply-edits roundtrip."""
    print("[10/16] landing mode (no API)")
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
        "brief": "LongcatDesign v1.0 landing",
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
                  "text": "LongcatDesign", "font_family": "NotoSerifSC-Bold",
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
            {"layer_id": "S3", "name": "cta", "kind": "section", "z_index": 3,
             "children": [
                 {"layer_id": "C1", "name": "cta_text", "kind": "text", "z_index": 1,
                  "text": "pip install longcat-design",
                  "font_family": "NotoSansSC-Bold", "font_size_px": 36,
                  "align": "center", "effects": {"fill": "#f8fafc"}},
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
        _fail(f"landing composite: {obs.summary}")
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

    traj, traj_path = apply_edits(
        edited_path, settings=settings,
        out_dir=out_dir / "restored",
    )
    if traj.metadata.get("parent_run_id") != "smoke-landing":
        _fail(f"landing round-trip lost parent_run_id: got "
              f"{traj.metadata.get('parent_run_id')!r}")
    if traj.design_spec.artifact_type != ArtifactType.LANDING:
        _fail("landing round-trip lost artifact_type")
    # Layer graph should have 3 sections + their children preserved
    sections = [n for n in traj.layer_graph if n.kind == "section"]
    if len(sections) != 3:
        _fail(f"expected 3 sections, got {len(sections)}")
    # Headline font size should reflect the edit (128, not 96)
    headline = next((c for s in sections for c in (s.children or [])
                     if c.name == "hero_headline"), None)
    if headline is None:
        _fail("hero_headline lost on round-trip")
    if headline.font_size_px != 128:
        _fail(f"edit lost: hero_headline.font_size_px={headline.font_size_px}, expected 128")
    _ok(f"landing round-trip: 3 sections + children preserved, edits applied "
        f"(hero_headline: 96px → 128px)")


def check_design_system_styles() -> None:
    """Render a landing in each of the 6 bundled styles, verify the matching
    CSS got inlined and the style-specific signature tokens are present."""
    print("[11/16] design-system styles (no API)")
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
    print("[12/16] landing with images (no API)")
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
        "figure.layer.image":     '<figure class="layer image"',
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
    traj, _ = apply_edits(
        Path(comp.html_path), settings=settings,
        out_dir=out_dir / "restored",
    )
    sections = [n for n in traj.layer_graph if n.kind == "section"]
    all_children = [c for s in sections for c in (s.children or [])]
    image_kids = [c for c in all_children if c.kind == "image"]
    if len(image_kids) != 2:
        _fail(f"round-trip lost images: expected 2, got {len(image_kids)}")
    for img in image_kids:
        if not img.src_path or not Path(img.src_path).exists():
            _fail(f"round-trip image has no file: {img.layer_id} src={img.src_path}")
    _ok(f"round-trip: {len(sections)} sections + {len(image_kids)} image layers "
        f"all restored with src_path decoded from data: URI")


def check_deck_mode() -> None:
    """Deck end-to-end: slide-tree spec → PPTX + per-slide PNGs + preview grid.
    No API — python-pptx writes a real .pptx that we reopen + verify."""
    print("[13/16] deck mode (no API)")
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
        _fail(f"propose_design_spec(deck): {obs.summary}")

    obs = composite({}, ctx=ctx)
    if obs.status != "ok":
        _fail(f"composite(deck): {obs.summary}")

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

    pptx_size_kb = Path(comp.pptx_path).stat().st_size // 1024
    preview_size_kb = Path(comp.preview_path).stat().st_size // 1024
    _ok(f"deck composite: 3 slides → {pptx_size_kb} KB .pptx + "
        f"3 slide PNGs + {preview_size_kb} KB preview grid")
    _ok("pptx reopen: slide count + native text runs + picture shape all OK")


def check_reasoning_step_roundtrip() -> None:
    """v1 training-data schema: reasoning step + ThinkingBlockRecord survive roundtrip.

    Covers three subcases:
      1. Plain thinking block (thinking + signature non-empty, is_redacted=False)
      2. Redacted thinking block (thinking empty, signature carries opaque data)
      3. The new AgentTraceStep fields (thinking_blocks, stop_reason,
         cache_read_input_tokens, cache_creation_input_tokens)
    The existing pydantic-roundtrip check (#3) would catch basic missing-field
    bugs; this one specifically exercises the CoT capture path with the exact
    shape the planner will emit at runtime.
    """
    print("[14/16] reasoning step + ThinkingBlockRecord roundtrip")
    plain = ThinkingBlockRecord(
        thinking="I need to first declare the artifact type, then propose a spec.",
        signature="sig_abc123_opaque_anthropic_signature",
        is_redacted=False,
    )
    redacted = ThinkingBlockRecord(
        thinking="",
        signature="enc_encrypted_payload_opaque_bytes",
        is_redacted=True,
    )
    step = AgentTraceStep(
        step_idx=42,
        timestamp=datetime.now(),
        actor="planner",
        type="reasoning",
        thinking_blocks=[plain, redacted],
        stop_reason="tool_use",
        cache_read_input_tokens=1234,
        cache_creation_input_tokens=567,
        model="claude-opus-4-7",
    )
    critic_step = AgentTraceStep(
        step_idx=43,
        timestamp=datetime.now(),
        actor="critic",
        type="reasoning",
        thinking_blocks=[ThinkingBlockRecord(
            thinking="The title contrast is fine; the stamp is too small.",
            signature="sig_critic_789",
        )],
        model="claude-opus-4-7",
    )
    traj = Trajectory(
        run_id="smoke_reasoning",
        created_at=datetime.now(),
        brief="smoke",
        design_spec=DesignSpec(
            brief="smoke",
            canvas={"w_px": 100, "h_px": 100, "dpi": 72,
                    "aspect_ratio": "1:1", "color_mode": "RGB"},
        ),
        layer_graph=[],
        agent_trace=[step, critic_step],
        composition=CompositionArtifacts(layer_manifest=[]),
        metadata={
            "version": "v1",
            "planner_thinking_budget": 10000,
            "critic_thinking_budget": 10000,
            "interleaved_thinking": True,
        },
    )
    dumped = traj.model_dump(mode="json")
    serialized = json.dumps(dumped, ensure_ascii=False)
    try:
        reloaded = Trajectory.model_validate(json.loads(serialized))
    except ValidationError as e:
        _fail(f"reasoning trajectory roundtrip: {e.errors()[:3]}")

    reasoning_steps = [s for s in reloaded.agent_trace if s.type == "reasoning"]
    if len(reasoning_steps) != 2:
        _fail(f"expected 2 reasoning steps after roundtrip, got {len(reasoning_steps)}")
    planner_step = next(s for s in reasoning_steps if s.actor == "planner")
    if not planner_step.thinking_blocks or len(planner_step.thinking_blocks) != 2:
        _fail("planner reasoning step lost thinking_blocks")
    if planner_step.thinking_blocks[0].thinking != plain.thinking:
        _fail("thinking text corrupted in roundtrip")
    if planner_step.thinking_blocks[0].signature != plain.signature:
        _fail("thinking signature corrupted in roundtrip")
    if not planner_step.thinking_blocks[1].is_redacted:
        _fail("redacted thinking flag not preserved")
    if planner_step.stop_reason != "tool_use":
        _fail(f"stop_reason lost: {planner_step.stop_reason}")
    if planner_step.cache_read_input_tokens != 1234:
        _fail(f"cache_read tokens lost: {planner_step.cache_read_input_tokens}")
    if reloaded.metadata.get("version") != "v1":
        _fail(f"metadata.version != v1, got {reloaded.metadata.get('version')}")

    # Backward compat: an old v0 trajectory without these fields still loads.
    legacy_step = AgentTraceStep(
        step_idx=1, timestamp=datetime.now(),
        actor="user", type="input", text="brief",
    )
    _ = legacy_step.model_dump(mode="json")
    assert legacy_step.thinking_blocks is None
    assert legacy_step.stop_reason is None

    _ok(f"reasoning step + 2 thinking blocks + cache tokens roundtrip ({len(serialized)} bytes)")


def check_ingest_document_markdown() -> None:
    """Markdown ingestion: seed a stub .md with a relative image ref, verify
    ingest_document registers the image in rendered_layers + returns the raw
    text. No API — markdown path doesn't call Anthropic."""
    print("[15/16] ingest_document markdown (no API)")
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
        _fail(f"ingest_document(markdown): {obs.summary}")

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
    print("[16/16] ingest_document image (no API)")
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
        _fail(f"ingest_document(image): {obs.summary}")

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
    print("\n  smoke test passed.")
    print("  artifacts in: out/smoke/, out/smoke_edit/, out/smoke_apply/, "
          "out/smoke_landing/, out/smoke_styles/, out/smoke_landing_img/, "
          "out/smoke_deck/, out/smoke_ingest_md/, out/smoke_ingest_image/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
