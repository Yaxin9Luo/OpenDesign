"""Tool registry — name → (JSON schema, handler).

Schemas exposed verbatim to the Anthropic tool-use API. Handlers signature:
    fn(args: dict, *, ctx: ToolContext) -> ToolObservation
"""

from __future__ import annotations

from ._contract import ToolContext, ToolHandler  # re-export
from .composite import composite
from .critique_tool import critique
from .edit_layer import edit_layer
from .fetch_brand_asset import fetch_brand_asset
from .finalize import finalize
from .generate_background import generate_background
from .generate_image import generate_image
from .ingest_document import ingest_document
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
        "name": "ingest_document",
        "description": (
            "Read user-provided source files (PDF papers, markdown notes, or "
            "image files) and extract a structured manifest the planner can map "
            "to a DesignSpec. Call this FIRST when the brief prologue mentions "
            "'Attached files:' — BEFORE propose_design_spec — so the returned "
            "title / sections / figures drive the spec you write next. PDF "
            "figures are extracted (via pymupdf page-render + Claude vision "
            "bbox location) and PRE-REGISTERED in rendered_layers with stable "
            "layer_ids — reference them as image children in your DesignSpec "
            "with kind: \"image\" and the composite step hydrates src_path "
            "automatically. Standalone .png/.jpg files get a passthrough "
            "layer_id. Markdown ingestion returns raw text + any resolved "
            "relative image refs. Returns ToolObservation; artifacts are the "
            "extracted / copied image PNG paths."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Absolute or ~-prefixed paths to ingest. Supported "
                        "extensions: .pdf (academic papers / reports, "
                        "Anthropic native document vision), .md/.markdown/.txt "
                        "(markdown or plain text, embedded image refs copied), "
                        ".png/.jpg/.jpeg/.webp (single-image passthrough)."
                    ),
                },
            },
            "required": ["file_paths"],
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
        "name": "generate_image",
        "description": (
            "Generate an inline image for a landing-page section via Gemini "
            "Nano Banana Pro. Use this for landing hero visuals (product "
            "photography, brand illustration) and feature-card icons/art. "
            "Images are TEXT-FREE — the renderer overlays real HTML text. "
            "Output is stored as a `kind: \"image\"` layer with a PNG file; "
            "you then reference it in a section's `children` list so the "
            "landing HTML renderer emits `<img>` inline with the flow text. "
            "NOT for posters — poster uses generate_background with safe_zones. "
            "Prompting tip: use the per-style imagery-prompt prefix from the "
            "design-system guide (e.g. 'soft 3D clay render, pastel palette' "
            "for claymorphism) so all images on the same landing feel "
            "stylistically coherent. "
            "Returns ToolObservation; artifact is the PNG path."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "layer_id": {"type": "string"},
                "name": {
                    "type": "string",
                    "description": "Semantic name: 'hero_image' | 'feature_1_icon' | 'cta_banner' etc.",
                },
                "prompt": {
                    "type": "string",
                    "description": (
                        "Scene-only description (no text, no logos). Include "
                        "the design-system style prefix. Example: 'soft 3D clay "
                        "render of a warm milk tea in a glass cup, pastel "
                        "off-white background, gentle top-light, rounded forms, "
                        "peaceful handcrafted feel'."
                    ),
                },
                "aspect_ratio": {
                    "type": "string",
                    "enum": ["1:1", "3:4", "4:3", "16:9", "9:16",
                             "2:3", "3:2", "4:5", "5:4", "21:9"],
                    "description": (
                        "Typical picks: '16:9' or '3:2' for hero, '1:1' for "
                        "feature icons, '4:3' for mid-section banners."
                    ),
                },
                "image_size": {
                    "type": "string", "enum": ["1K", "2K"],
                    "description": "'1K' for feature icons (cheaper); '2K' for hero (higher quality).",
                },
                "z_index": {"type": "integer"},
            },
            "required": ["layer_id", "name", "prompt", "aspect_ratio", "image_size"],
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
        "name": "edit_layer",
        "description": (
            "Apply a targeted subset-diff to an existing TEXT layer — tweak its "
            "text/font/color/size/bbox/effects without re-declaring the whole "
            "LayerNode or re-rendering the rest of the artifact. Handler reads "
            "the current layer from ctx.state.rendered_layers[layer_id], merges "
            "`diff` (nested merge for bbox + effects, replace otherwise), and "
            "overwrites the layer's PNG in place. No implicit composite — call "
            "`composite` after batching all your edits. "
            "Text layers only: for backgrounds call generate_background with "
            "the same layer_id; for brand assets call fetch_brand_asset. "
            "Use this for 'make the title bigger', 'try red', 'move the stamp "
            "down 40px', 'bolder shadow' — anything that tweaks one layer. "
            "Returns ToolObservation{status, summary, artifacts}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "layer_id": {
                    "type": "string",
                    "description": (
                        "ID of the existing text layer to edit. Must match a "
                        "layer_id present in ctx.state.rendered_layers."
                    ),
                },
                "diff": {
                    "type": "object",
                    "description": (
                        "Subset of editable fields. Only provide what you want "
                        "to change; other fields keep their current value."
                    ),
                    "properties": {
                        "text": {"type": "string"},
                        "font_family": {
                            "type": "string",
                            "description": (
                                "'NotoSansSC-Bold' or 'NotoSerifSC-Bold' "
                                "(unknown families fall back to NotoSansSC-Bold)."
                            ),
                        },
                        "font_size_px": {"type": "integer"},
                        "fill": {"type": "string", "description": "Hex color"},
                        "bbox": {
                            "type": "object",
                            "description": (
                                "Partial bbox update. Fields you omit keep "
                                "their existing value."
                            ),
                            "properties": {
                                "x": {"type": "integer"},
                                "y": {"type": "integer"},
                                "w": {"type": "integer"},
                                "h": {"type": "integer"},
                            },
                        },
                        "align": {
                            "type": "string",
                            "enum": ["left", "center", "right"],
                        },
                        "z_index": {"type": "integer"},
                        "effects": {
                            "type": "object",
                            "description": (
                                "Partial effects update. Nested merge: shadow "
                                "+ stroke sub-objects are replaced whole."
                            ),
                            "properties": {
                                "stroke": {"type": "object"},
                                "shadow": {"type": "object"},
                            },
                        },
                    },
                    "additionalProperties": False,
                },
            },
            "required": ["layer_id", "diff"],
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
    "ingest_document": ingest_document,
    "propose_design_spec": propose_design_spec,
    "generate_background": generate_background,
    "generate_image": generate_image,
    "render_text_layer": render_text_layer,
    "edit_layer": edit_layer,
    "fetch_brand_asset": fetch_brand_asset,
    "composite": composite,
    "critique": critique,
    "finalize": finalize,
}


__all__ = ["TOOL_SCHEMAS", "TOOL_HANDLERS", "ToolContext"]
