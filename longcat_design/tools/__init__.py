"""Tool registry — name → (JSON schema, handler).

Schemas exposed verbatim to the Anthropic tool-use API. Handlers signature:
    fn(args: dict, *, ctx: ToolContext) -> ToolObservation
"""

from __future__ import annotations

from ._contract import ToolContext, ToolHandler  # re-export
from .composite import composite
from .critique_tool import critique
from .fetch_brand_asset import fetch_brand_asset
from .finalize import finalize
from .generate_background import generate_background
from .propose_design_spec import propose_design_spec
from .render_text_layer import render_text_layer
from .switch_artifact_type import switch_artifact_type


TOOL_SCHEMAS: list[dict] = [
    {
        "name": "switch_artifact_type",
        "description": (
            "Declare what kind of design artifact this session is producing: "
            "'poster' (absolutely-positioned layered visual), 'deck' (N-slide "
            "presentation, PPTX-native), or 'landing' (self-contained HTML "
            "one-pager with semantic sections). Call this AT THE START of a "
            "new artifact (first turn, or mid-session when the user asks for a "
            "different artifact type) BEFORE propose_design_spec. Default is "
            "'poster' if you skip this; but calling explicitly is recommended "
            "so the decision lands in the trajectory as its own event. "
            "Returns ToolObservation{status, summary, next_actions, artifacts}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["poster", "deck", "landing"],
                    "description": (
                        "The artifact type. 'poster' = vertical/horizontal "
                        "absolutely-positioned layered visual. 'deck' = N slides "
                        "with PPTX-editable text frames. 'landing' = single "
                        "self-contained HTML page with flow layout."
                    ),
                },
            },
            "required": ["type"],
        },
    },
    {
        "name": "propose_design_spec",
        "description": (
            "Submit the initial DesignSpec for this brief. Call this AFTER "
            "switch_artifact_type (or as the first tool if you're defaulting "
            "to poster). The runner validates and stores the spec; subsequent "
            "tool calls operate on this spec. Re-call to revise. "
            "If `design_spec.artifact_type` is omitted, it falls back to the "
            "value set by switch_artifact_type (default: 'poster'). "
            "Returns ToolObservation{status, summary, next_actions, artifacts}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "design_spec": {
                    "type": "object",
                    "description": (
                        "Full DesignSpec JSON. Required keys: brief, canvas "
                        "({w_px, h_px, dpi, aspect_ratio, color_mode}), palette "
                        "(list of hex strings), typography ({title_font, "
                        "subtitle_font, ...}), mood (list[str]), composition_notes "
                        "(str), layer_graph (list of LayerNode skeletons — "
                        "src_path/prompt filled by later tool calls)."
                    ),
                },
            },
            "required": ["design_spec"],
        },
    },
    {
        "name": "generate_background",
        "description": (
            "Generate a TEXT-FREE background raster via Gemini Nano Banana Pro. "
            "The prompt MUST describe a scene with NO text, characters, lettering, "
            "symbols, or logos — a separate text rendering pass overlays editable "
            "type. Returns ToolObservation; artifact is the PNG path."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "layer_id": {"type": "string"},
                "prompt": {
                    "type": "string",
                    "description": (
                        "Scene-only description. The pipeline will append "
                        "'No text, no characters, no lettering, no symbols, no "
                        "logos, no watermarks.' if you don't include it yourself."
                    ),
                },
                "aspect_ratio": {
                    "type": "string",
                    "enum": ["1:1", "3:4", "4:3", "16:9", "9:16",
                             "2:3", "3:2", "4:5", "5:4", "21:9"],
                },
                "image_size": {"type": "string", "enum": ["1K", "2K"]},
                "safe_zones": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "x": {"type": "integer"},
                            "y": {"type": "integer"},
                            "w": {"type": "integer"},
                            "h": {"type": "integer"},
                            "purpose": {"type": "string"},
                        },
                        "required": ["x", "y", "w", "h", "purpose"],
                    },
                    "description": (
                        "Regions reserved for later text overlay. Bias the prompt "
                        "to keep these areas low-detail (e.g. 'centered subject "
                        "leaving top 30% as calm sky')."
                    ),
                },
            },
            "required": ["layer_id", "prompt", "aspect_ratio", "image_size"],
        },
    },
    {
        "name": "render_text_layer",
        "description": (
            "Rasterize a text run into a transparent RGBA PNG sized to the canvas. "
            "Pillow only. Supports stroke and shadow effects. "
            "Returns ToolObservation; artifact is the PNG path."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "layer_id": {"type": "string"},
                "name": {
                    "type": "string",
                    "description": "Semantic name: 'title' | 'subtitle' | 'stamp' | 'tagline' | …",
                },
                "text": {"type": "string"},
                "font_family": {
                    "type": "string",
                    "description": (
                        "One of: 'NotoSansSC-Bold' (default for Latin/subtitle), "
                        "'NotoSerifSC-Bold' (default for Chinese title). "
                        "Unknown families fall back to NotoSansSC-Bold with a warning."
                    ),
                },
                "font_size_px": {"type": "integer"},
                "fill": {"type": "string", "description": "Hex color, e.g. '#1a0f0a'"},
                "bbox": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "integer"},
                        "y": {"type": "integer"},
                        "w": {"type": "integer"},
                        "h": {"type": "integer"},
                    },
                    "required": ["x", "y", "w", "h"],
                    "description": "Top-left origin, pixel coords on full canvas.",
                },
                "align": {"type": "string", "enum": ["left", "center", "right"]},
                "z_index": {"type": "integer"},
                "effects": {
                    "type": "object",
                    "properties": {
                        "stroke": {
                            "type": "object",
                            "properties": {
                                "color": {"type": "string"},
                                "width": {"type": "integer"},
                            },
                        },
                        "shadow": {
                            "type": "object",
                            "properties": {
                                "color": {"type": "string"},
                                "dx": {"type": "integer"},
                                "dy": {"type": "integer"},
                                "blur": {"type": "integer"},
                            },
                        },
                    },
                },
            },
            "required": ["layer_id", "name", "text", "font_family",
                         "font_size_px", "fill", "bbox"],
        },
    },
    {
        "name": "fetch_brand_asset",
        "description": (
            "v0 STUB: always returns ToolObservation{status:'not_found'}. "
            "Reserved for v1 Brand Kit. Planner should fall back to generated "
            "or composed elements."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"asset_id": {"type": "string"}},
            "required": ["asset_id"],
        },
    },
    {
        "name": "composite",
        "description": (
            "Combine all currently rendered layers into PSD (psd-tools, pixel "
            "layers with semantic names + 'text' group), SVG (real <text> "
            "elements + base64-embedded background + subsetted-WOFF2 fonts in "
            "@font-face), and a flattened preview PNG. Reads layers from runner "
            "state — no need to pass layer_graph again. "
            "Returns ToolObservation; artifacts = [psd_path, svg_path, preview_path]."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "critique",
        "description": (
            "Run a vision-based self-critique on the latest preview.png against "
            "design_spec. Returns ToolObservation{summary} plus a CritiqueResult "
            "JSON in artifacts[0]. Use AT MOST max_critique_iters times. If "
            "verdict='revise', adjust text layers (positions/colors/sizes) and "
            "call composite again; do NOT regenerate background unless a blocker "
            "requires it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "preview_path": {
                    "type": "string",
                    "description": "Optional override; defaults to last composite output.",
                },
            },
        },
    },
    {
        "name": "finalize",
        "description": (
            "Signal that the design is done. Runner serializes the full Trajectory "
            "(brief + design_spec + layer_graph + agent_trace + critique_loop + "
            "composition + metadata) to trajectories/<run_id>.json. "
            "Returns ToolObservation; trajectory file path is added by the runner."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "notes": {
                    "type": "string",
                    "description": "Optional final notes recorded in trajectory metadata.",
                },
            },
        },
    },
]


TOOL_HANDLERS: dict[str, ToolHandler] = {
    "switch_artifact_type": switch_artifact_type,
    "propose_design_spec": propose_design_spec,
    "generate_background": generate_background,
    "render_text_layer": render_text_layer,
    "fetch_brand_asset": fetch_brand_asset,
    "composite": composite,
    "critique": critique,
    "finalize": finalize,
}


__all__ = ["TOOL_SCHEMAS", "TOOL_HANDLERS", "ToolContext"]
