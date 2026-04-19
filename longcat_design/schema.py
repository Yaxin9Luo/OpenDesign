"""Pydantic models — single source of truth for the trajectory data shape.

Every field is designed to support multi-task SFT extraction:
- (brief, design_spec) -> planner SFT
- (design_spec, layer_graph) -> layered-gen SFT (Longcat-Next core target)
- (layer.prompt, layer.src_path) -> image-gen SFT
- agent_trace with text + tool_call + tool_result -> CoT/reasoning SFT
- critique_loop pre/post layer_graph snapshots -> DPO pairs
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


LandingStyle = Literal[
    "minimalist", "editorial", "neubrutalism",
    "glassmorphism", "claymorphism", "liquid-glass",
]


class DesignSystem(BaseModel):
    """Landing-specific design-system selector (v1.0 #8.5).

    One of the six bundled styles. The HTML renderer loads the matching
    `assets/design-systems/<style>.css` at render time and inlines it into
    the output HTML, so the file stays self-contained after distribution.
    """
    style: LandingStyle = "minimalist"
    accent_color: str | None = None      # override the style's --ld-accent token
    font_pairing: str | None = None      # free-text planner hint, not enforced


class ArtifactType(str, Enum):
    """What kind of design artifact is being produced in the current session slot.

    Drives renderer selection and prompts the planner with artifact-specific
    layout guidance. A chat session may contain multiple artifacts (mix of
    types); the `switch_artifact_type` tool changes this mid-session.
    """
    POSTER = "poster"       # vertical / horizontal, absolutely-positioned layers
    DECK = "deck"           # N slides, PPTX-native editability
    LANDING = "landing"     # self-contained HTML one-pager with flow layout


Status = Literal["ok", "error", "partial", "not_found"]
Actor = Literal["user", "planner", "tool", "critic", "system"]
StepType = Literal[
    "input", "thought", "tool_call", "tool_result",
    "design_spec", "critique", "finalize",
    "artifact_switch",  # new v1.0: emitted when switch_artifact_type is called
]
LayerKind = Literal["background", "text", "brand_asset", "group", "section"]
Verdict = Literal["pass", "revise", "fail"]
Severity = Literal["blocker", "major", "minor"]
IssueCategory = Literal[
    "typography", "composition", "brand",
    "legibility", "cultural", "artifact",
]


class SafeZone(BaseModel):
    """Top-left origin, pixel units. Used for both bbox and reserved regions."""
    x: int
    y: int
    w: int
    h: int
    purpose: Literal["title", "subtitle", "stamp", "logo", "body"] | None = None

    @field_validator("w", "h")
    @classmethod
    def _positive_dim(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("w/h must be positive")
        return v

    @field_validator("x", "y")
    @classmethod
    def _nonneg_pos(cls, v: int) -> int:
        if v < 0:
            raise ValueError("x/y must be >= 0")
        return v


class TextEffect(BaseModel):
    stroke: dict[str, Any] | None = None     # {color: "#hex", width: int}
    shadow: dict[str, Any] | None = None     # {color: "#hex", dx: int, dy: int, blur: int}
    fill: str = "#000000"


class LayerNode(BaseModel):
    """Polymorphic layer descriptor. Fields populated depend on `kind`.

    `bbox` is optional as of v1.0 #8: poster/deck layers use pixel coords;
    landing layers (kind="section" and their text children) use flow layout
    with no pixel bbox.
    """
    layer_id: str
    name: str
    kind: LayerKind
    z_index: int
    bbox: SafeZone | None = None

    # text-only
    text: str | None = None
    font_family: str | None = None
    font_size_px: int | None = None
    align: Literal["left", "center", "right"] | None = None
    effects: TextEffect | None = None

    # background-only
    prompt: str | None = None
    aspect_ratio: str | None = None
    image_size: str | None = None

    # any
    src_path: str | None = None
    children: list["LayerNode"] = Field(default_factory=list)


class DesignSpec(BaseModel):
    brief: str
    artifact_type: ArtifactType = ArtifactType.POSTER
    canvas: dict[str, Any]                   # {w_px, h_px, dpi, aspect_ratio, color_mode:"RGB"}
    palette: list[str] = Field(default_factory=list)
    typography: dict[str, str] = Field(default_factory=dict)
    mood: list[str] = Field(default_factory=list)
    composition_notes: str = ""
    layer_graph: list[LayerNode] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)
    design_system: DesignSystem | None = None  # landing-only (v1.0 #8.5)

    @model_validator(mode="after")
    def _canvas_required_keys(self) -> "DesignSpec":
        for k in ("w_px", "h_px"):
            if k not in self.canvas:
                raise ValueError(f"canvas missing required key: {k}")
        return self


class ToolObservation(BaseModel):
    """Universal tool return contract — see agent-harness-construction skill."""
    status: Status
    summary: str
    next_actions: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)


class AgentTraceStep(BaseModel):
    step_idx: int
    timestamp: datetime
    actor: Actor
    type: StepType
    tool_use_id: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    observation: ToolObservation | None = None
    text: str | None = None
    spec_snapshot: DesignSpec | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    model: str | None = None


class CritiqueIssue(BaseModel):
    severity: Severity
    layer_id: str | None = None
    category: IssueCategory
    description: str
    suggested_fix: str


class CritiqueResult(BaseModel):
    iteration: int
    verdict: Verdict
    score: float
    issues: list[CritiqueIssue] = Field(default_factory=list)
    rationale: str = ""

    @field_validator("score")
    @classmethod
    def _score_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("score must be in [0, 1]")
        return v


class CompositionArtifacts(BaseModel):
    """Paths are per-artifact-type: poster produces PSD+SVG+HTML+preview;
    landing (v1.0 #8) produces HTML+preview only (no PSD/SVG); deck (#7)
    will add PPTX. `None` means "not applicable for this artifact type"."""
    psd_path: str | None = None
    svg_path: str | None = None
    html_path: str | None = None        # v1.0 #6 — self-contained, contenteditable
    preview_path: str | None = None
    layer_manifest: list[dict[str, Any]] = Field(default_factory=list)


class Trajectory(BaseModel):
    run_id: str
    created_at: datetime
    brief: str
    design_spec: DesignSpec
    layer_graph: list[LayerNode]
    agent_trace: list[AgentTraceStep]
    critique_loop: list[CritiqueResult] = Field(default_factory=list)
    composition: CompositionArtifacts
    metadata: dict[str, Any] = Field(default_factory=dict)
