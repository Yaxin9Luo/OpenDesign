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
    print("[1/6] imports")
    from . import cli, config, critic, planner, runner, schema  # noqa
    from .tools import (
        TOOL_HANDLERS, TOOL_SCHEMAS, ToolContext,
        composite, critique_tool, fetch_brand_asset, finalize,
        generate_background, propose_design_spec, render_text_layer,
    )  # noqa
    from .util import ids, io, logging  # noqa
    _ok("all modules import")


def check_tool_registry() -> None:
    print("[2/6] tool registry")
    from .tools import TOOL_HANDLERS, TOOL_SCHEMAS

    expected = {"propose_design_spec", "generate_background", "render_text_layer",
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
    _ok(f"7 tools wired (schemas + handlers): {sorted(schema_names)}")


def check_pydantic_roundtrip() -> None:
    print("[3/6] pydantic schema round-trip")
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
    print("[4/6] fonts")
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
    """Build a fake background + 2 real text layers, run composite end-to-end."""
    print("[5/6] composite (no API)")
    from .config import REPO_ROOT, Settings
    from .tools import ToolContext
    from .tools.composite import composite
    from .tools.propose_design_spec import propose_design_spec
    from .tools.render_text_layer import render_text_layer

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
                     ("preview", comp.preview_path)]:
        path = Path(p)
        if not path.exists() or path.stat().st_size == 0:
            _fail(f"{label} not written: {p}")
        _ok(f"{label} {path.name} ({path.stat().st_size // 1024} KB)")


def check_svg_text_is_vector() -> None:
    print("[6/6] SVG vector text")
    from .config import REPO_ROOT
    svg_path = REPO_ROOT / "out" / "smoke" / "poster.svg"
    if not svg_path.exists():
        _fail("smoke SVG not found — composite step likely failed")
    text = svg_path.read_text(encoding="utf-8")
    if "<text" not in text:
        _fail("SVG has no <text> element — text was rasterized!")
    if "国宝回家" not in text:
        _fail("Chinese title not present as vector text in SVG")
    if "@font-face" not in text:
        print("  warn  no @font-face block (font subsetting may have failed; "
              "SVG will rely on system fonts)")
    else:
        _ok("@font-face block embedded with subsetted WOFF2")
    _ok("SVG contains <text>国宝回家</text> as real vector")


def main() -> int:
    check_imports()
    check_tool_registry()
    check_pydantic_roundtrip()
    check_fonts()
    check_composite_no_api()
    check_svg_text_is_vector()
    print("\n  smoke test passed.")
    print("  artifacts in: out/smoke/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
