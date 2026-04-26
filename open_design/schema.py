"""Pydantic models — two layers:

1. **Runtime models** (DesignSpec / LayerNode / CritiqueResult / ...):
   the shapes tools and the critic pass around in memory. These are the
   engineering primitives — NOT persisted in the final training-data
   record.

2. **Training-data models** (DistillTrajectory + AgentTraceStep +
   ToolResultRecord + ThinkingBlockRecord + TrainingMetadata): the
   on-disk `out/trajectories/<run_id>.json` shape. These are purpose-
   built for mid-training SFT + RL post-training:

   - thinking blocks (plain + redacted) captured verbatim with signatures
   - full tool_use args as emitted by the model
   - lean tool_result payload (IDs + sha256 + minimal state; NO paths,
     NO descriptive summary, NO next-action hints — hints would leak
     into the policy and cause reward hacking)
   - episode-level reward (final_reward + terminal_status)
   - per-turn usage for cost-aware RL reward shaping

   Deliberately absent from DistillTrajectory: file paths, timestamps,
   design_spec / layer_graph / composition (these live on disk under
   out/runs/<run_id>/ if needed for inspection).

Schema version: v2 (see docs/DATA-CONTRACT.md for evolution log).
"""

from __future__ import annotations

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
    # v1.3 tri-state — None = auto (nav rendered when section_count >= 4),
    # True/False = explicit opt-in/out. Renderer at render time.
    show_nav: bool | None = None


# v2.5.2 deck design system — separate enum from LandingStyle because deck
# visual idioms (footer, slide numbers, divider slides) have no landing
# analog. First system: academic-editorial (cream + oxblood + PlayfairDisplay).
# Add additional members here as future systems land (e.g. "tech-keynote",
# "consumer-pitch") + ship matching `assets/deck_templates/<style>.pptx` and
# `prompts/design-systems/deck-<style>.md`.
DeckStyle = Literal["academic-editorial"]


class DeckDesignSystem(BaseModel):
    """Deck-specific design-system selector (v2.5.2).

    Mirrors `DesignSystem` for landing but routes to a `.pptx` template
    rather than a CSS bundle. The renderer opens
    `assets/deck_templates/<style>.pptx`, clones layouts per `slide.role`,
    fills text shapes by `template_slot`, and places images at slot bboxes.
    """
    style: DeckStyle = "academic-editorial"
    accent_color: str | None = None      # override the template's accent token
    footer_text: str | None = None       # paper title (or short_title) shown in
                                         # content-slide footers; renderer auto-
                                         # populates if None and brief contains
                                         # an obvious title.
    badge_text: str | None = None        # cover badge ("NeurIPS 2026", "arXiv");
                                         # planner can override or runner derives
                                         # from ingest metadata.


class ArtifactType(str, Enum):
    """What kind of design artifact is being produced in the current session slot.

    Drives renderer selection and prompts the planner with artifact-specific
    layout guidance. A chat session may contain multiple artifacts (mix of
    types); the `switch_artifact_type` tool changes this mid-session.
    """
    POSTER = "poster"       # vertical / horizontal, absolutely-positioned layers
    DECK = "deck"           # N slides, PPTX-native editability
    LANDING = "landing"     # self-contained HTML one-pager with flow layout


Status = Literal["ok", "error"]
Actor = Literal["user", "planner", "tool", "critic"]
StepType = Literal[
    "input",        # user brief — exactly one per trajectory, the training input root
    "reasoning",    # extended-thinking blocks emitted by planner OR critic
    "tool_call",    # planner emits a tool_use block
    "tool_result",  # environment returns a ToolResultRecord
    "finalize",     # terminal step — only emitted by the finalize tool path
]
ErrorCategory = Literal[
    "validation",        # tool_args failed pydantic / schema validation
    "safety_filter",     # NBP / Anthropic safety filter rejected the request
    "api",               # upstream API error (network / 5xx / auth)
    "timeout",           # call exceeded its budget
    "not_found",         # referenced ID / asset doesn't exist
    "unsupported_format",  # ingest_document on an unrecognized file type
    "parse_error",       # critic / ingest model output failed to parse
    "unknown",
]
LayerKind = Literal[
    "background",    # full-canvas raster (poster/deck only)
    "text",          # rendered text layer (poster) OR inline HTML text (landing)
    "brand_asset",   # user-supplied brand imagery (v1 stub)
    "group",         # organisational grouping (unused in v1.0)
    "section",       # landing section container (v1.0 #8)
    "image",         # NBP-generated inline image inside a landing section (v1.0 #8.75)
    "slide",         # deck slide container: children hold text/image elements (v1.0 #7)
    "table",         # v1.2 paper2any: structured data (rows/headers) — renderers
                     # produce native PPTX / HTML tables instead of cropped images.
                     # src_path holds a PIL-drawn PNG fallback for PSD/SVG paths.
    "cta",           # v1.3 landing call-to-action button — renders as <a role="button">
                     # with href + variant. Per-design-system styling via .ld-cta--*.
    "callout",       # v2.6 deck annotation: highlight a sub-region of a sibling
                     # picture/table, optional text label + arrow connector.
                     # Slide-only (kind="slide" parent). Renderer adds shapes
                     # ON TOP of the anchor's bbox via python-pptx primitives.
]
Verdict = Literal["pass", "revise", "fail"]
Severity = Literal["blocker", "major", "minor"]
IssueCategory = Literal[
    "typography", "composition", "brand",
    "legibility", "cultural", "artifact",
    # v1.0 #8.5-fix: landing critique often flags text-content concerns that
    # don't fit the poster-visual vocabulary — "copy" covers headline/body
    # wording quality, "content" covers section balance / length / pacing.
    "copy", "content",
    # v2.7 — fabricated number / unverifiable claim in body. Emitted by the
    # critic when a kind="text" body layer contains numeric tokens but no
    # `evidence_quote`, OR when the rendered slide contains "[?]" markers
    # left by the composite-stage provenance validator.
    "provenance",
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

    # v1.2 paper2any — table layers (kind="table") carry structured data
    # that renderers turn into native PPTX tables / HTML <table> elements.
    # `headers` is optional (first data row doubles as header when absent).
    # `caption` sits above or below the table depending on the renderer.
    # `src_path` is a PIL-rendered PNG fallback used by PSD/SVG paths that
    # don't have a live-table primitive.
    # `col_highlight_rule` — one entry per column, "max"/"min"/"" —
    # renderers bold the winning row per column. Enables "highlight
    # LongCat-Next's winning metrics" without the planner duplicating
    # every cell as bold/non-bold.
    rows: list[list[str]] | None = None
    headers: list[str] | None = None
    caption: str | None = None
    col_highlight_rule: list[str] | None = None

    # v1.3 landing interactivity — cta-only
    href: str | None = None
    variant: Literal["primary", "secondary", "ghost"] | None = None

    # v2.3 deck speaker notes — slide-only (kind="slide"); ignored on other kinds.
    # Populates `slide.notes_slide.notes_text_frame.text` in the PPTX renderer,
    # so the notes show in PowerPoint / Keynote presenter view but not on slides.
    speaker_notes: str | None = None

    # v2.5.2 deck templating — slide-only (kind="slide"); names which template
    # layout to clone. Renderer maps role → layout index; falls back to
    # "content" when None. Ignored when DesignSpec.deck_design_system is None
    # (blank-Presentation path).
    role: Literal[
        "cover", "content", "content_with_figure",
        "content_with_table", "section_divider", "closing",
    ] | None = None

    # v2.5.2 deck templating — child-of-slide; names the template shape this
    # child fills (e.g. "title", "body", "image_slot", "table_anchor"). The
    # renderer looks up the shape by name in the cloned layout and fills it.
    # Falls back to absolute bbox positioning when None or when the layout
    # has no shape with that name.
    template_slot: str | None = None

    # v2.6 callout — kind="callout" only; child of a slide alongside the
    # picture/table it annotates. Renderer overlays a shape (rectangle
    # highlight, ellipse circle, or textbox label) on top of the anchor's
    # bbox. Optional thin connector line from label to highlight. Used
    # for "presentation-native" emphasis on dense paper figures (e.g.
    # highlight the winning row of a benchmark table, label a panel of
    # an ablation grid).
    anchor_layer_id: str | None = None
                                  # references a sibling kind="image" /
                                  # "table" on the same slide; renderer
                                  # reads that shape's bbox to scope the
                                  # callout's coordinate space.
    callout_style: Literal["highlight", "label", "circle"] | None = None
                                  # highlight = rectangle outline only
                                  # label     = text box with thin border
                                  # circle    = ellipse outline
    callout_text: str | None = None  # label content (label style only)
    callout_region: SafeZone | None = None
                                  # sub-region within anchor.bbox to point
                                  # at, in image-pixel coords (top-left
                                  # origin relative to the anchor's bbox
                                  # top-left). If None, callout points at
                                  # the anchor's full bbox.
    arrow: bool = False           # if True + style=label, draw a thin
                                  # connector from label center to the
                                  # callout_region's nearest edge.

    # v2.7 Provenance — text-only; required when `text` contains a
    # significant numeric token (>=4 digits, decimal, or K/M/T/B/% suffix).
    # `evidence_quote` MUST be a verbatim substring of the ingested source
    # text; the composite-stage auditor (open_design/util/provenance.py)
    # rejects bullets that fail substring match. `evidence_source` is a
    # free-form trace hint ("ingest_table_06 row LongCat-Next" /
    # "ingest_p_12") used only for human inspection — not validated.
    # Motivation: 2026-04-25 longcat-next dogfood produced 9 fabricated
    # numbers across slides 4/6/8/9 (e.g. "PSNR 28.5 → 22.1 dB" with
    # paper-real values being 20.88/21.86/30.52/18.16). Pure prompt rules
    # backfired — the model met "number + named rival" by inventing
    # numbers. Machine-checkable provenance is the gate.
    evidence_quote: str | None = None
    evidence_source: str | None = None


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
    deck_design_system: DeckDesignSystem | None = None  # deck-only (v2.5.2)

    @model_validator(mode="after")
    def _canvas_required_keys(self) -> "DesignSpec":
        for k in ("w_px", "h_px"):
            if k not in self.canvas:
                raise ValueError(f"canvas missing required key: {k}")
        return self


class ThinkingBlockRecord(BaseModel):
    """One extended-thinking block captured from Claude's response.

    Anthropic returns two sub-types:
      - `thinking`: plain CoT text + opaque `signature` for verification
      - `redacted_thinking`: encrypted (text unavailable) + opaque `data`
        which we map onto `signature` to keep the record shape uniform.

    Both must round-trip back verbatim on the next turn or Anthropic 400s,
    so signatures are persisted even though we never interpret them.
    """
    thinking: str = ""              # empty when is_redacted=True
    signature: str = ""              # Anthropic-issued; opaque to us
    is_redacted: bool = False


class LegacyCritiqueIssue(BaseModel):
    """Legacy single-call critic issue (pre v2.7.3). Retained because old
    trajectories on disk reference this shape via `CritiqueResult.issues`.
    New code uses `CritiqueIssue` (the v2.7.3 sub-agent shape) below.
    """
    severity: Severity
    layer_id: str | None = None
    category: IssueCategory
    description: str
    suggested_fix: str

    @field_validator("category", mode="before")
    @classmethod
    def _coerce_unknown_category(cls, v: Any) -> Any:
        """Map unknown category strings to "artifact" rather than failing
        the whole CritiqueResult payload.

        Motivated by 2026-04-25 dogfood: Qwen-VL-Max returned
        `category="per-slide density"` (a sensible label, just not in our
        Literal). Pydantic's literal_error bubbled up, the entire critique
        was rejected, and the run terminated with reward=0.0 even though
        the deck itself was fine. Soft-coercing keeps the critic's signal
        usable while the rest of the schema stays strict.
        """
        if isinstance(v, str):
            allowed = {
                "typography", "composition", "brand", "legibility",
                "cultural", "artifact", "copy", "content",
                "provenance",  # v2.7
            }
            if v not in allowed:
                # Best-effort substring match before falling back to "artifact"
                # so "design_system_drift" still routes to "brand", etc.
                lower = v.lower()
                for cand in ("typography", "composition", "brand", "legibility",
                             "cultural", "copy", "content", "provenance"):
                    if cand in lower:
                        return cand
                return "artifact"
        return v


class CritiqueResult(BaseModel):
    """Legacy critic result (pre v2.7.3 inline-tool path). Retained so old
    trajectories on disk still load. The runner / planner now consumes
    `CritiqueReport` produced by `agents.critic_agent.CriticAgent`; this
    class only survives because `_derive_episode_outcome` and the chat
    helpers index past trajectories by its `verdict` / `score` shape, both
    of which the new `CritiqueReport` also exposes.
    """
    iteration: int
    verdict: Verdict
    score: float
    issues: list[LegacyCritiqueIssue] = Field(default_factory=list)
    rationale: str = ""

    @field_validator("score")
    @classmethod
    def _score_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("score must be in [0, 1]")
        return v


# v2.7.3 — vision critic sub-agent shape.
#
# The inline `Critic` class is gone; `agents.critic_agent.CriticAgent` now
# spawns its own LLM loop, sees slide PNGs (vision for ALL artifact types,
# not just poster), and emits one of these as the terminal `report_verdict`
# tool call. The planner consumes the JSON via the unchanged `critique`
# tool signature — see `tools/critique_tool.py`.
#
# Severity vocab is intentionally distinct from the legacy `Severity`
# (`major`/`minor`) because the sub-agent operates closer to the human
# code-review vocabulary (`high`/`medium`/`low`) and we don't want to
# silently overload the legacy enum.
CritiqueIssueSeverity = Literal["blocker", "high", "medium", "low"]
CritiqueIssueCategory = Literal[
    "provenance",          # number / quote / paper terminology not in source
    "claim_coverage",      # key paper claim not represented (v2.8.0 will wire)
    "visual_hierarchy",    # title vs body vs caption sizing / contrast
    "typography",          # font choice, leading, punctuation
    "layout",              # shape overlap / out-of-bounds / cramped slots
    "narrative_flow",      # slide ordering / transitions / arc
    "factual_error",       # asserts something the paper does not support
]
CritiqueVerdict = Literal["pass", "revise", "fail"]


class CritiqueIssue(BaseModel):
    """One concrete issue raised by the v2.7.3 vision critic sub-agent."""
    slide_id: str | None = None
    severity: CritiqueIssueSeverity
    category: CritiqueIssueCategory
    description: str
    evidence_paper_anchor: str | None = None


class CritiqueReport(BaseModel):
    """Terminal output of `CriticAgent.critique`. Embedded verbatim into
    the planner-facing `critique` tool_result.payload so the planner can
    decide between finalize / propose_design_spec / abort."""
    score: float
    verdict: CritiqueVerdict
    issues: list[CritiqueIssue] = Field(default_factory=list)
    summary: str = ""
    iteration: int = 1

    @field_validator("score")
    @classmethod
    def _score_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("score must be in [0, 1]")
        return v


class CompositionArtifacts(BaseModel):
    """Runtime model used by composite tool to track local file paths
    inside ctx.state. NOT persisted in DistillTrajectory (paths leak the
    user's file system; lean tool_result payload carries sha256 instead).
    apply_edits.py uses this for product round-trip — file paths there
    live on disk under out/runs/<run_id>/, not in the trajectory JSON."""
    psd_path: str | None = None
    svg_path: str | None = None
    html_path: str | None = None
    pptx_path: str | None = None
    preview_path: str | None = None
    layer_manifest: list[dict[str, Any]] = Field(default_factory=list)


# === Training-data models ===


class ToolResultRecord(BaseModel):
    """Lean tool result for distillation + RL training.

    Replaces the legacy `ToolObservation`. Designed to give the policy
    enough state to act on the next turn — and nothing else:

      - `status`: binary (RL reward signal base)
      - `error_message`: full text on error so the policy can learn
        recovery; never truncated
      - `error_category`: typed enum so reward models can distinguish
        model-side errors (validation) from environment errors
        (api / safety_filter / timeout)
      - `payload`: success-side minimal state — IDs the next tool_call
        must reference (layer_id, run_id), sha256 of artifacts so the
        policy can verify its own action produced a unique output, and
        for the critique tool the full CritiqueResult dump

    EXPLICITLY NOT INCLUDED: `summary` (descriptive log noise),
    `next_actions` (hint that would cause shortcut learning),
    `artifacts` (file paths, leak host filesystem).
    """
    status: Status
    error_message: str | None = None
    error_category: ErrorCategory | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class AgentTraceStep(BaseModel):
    """A single step in the agent's interaction loop. The 5 step types
    cover the entire trajectory; legacy装饰性 step types (thought /
    artifact_switch / design_spec / critique) are gone — their info is
    recoverable from tool_call args / tool_result payload."""
    step_idx: int
    actor: Actor
    type: StepType

    # Only on tool_call
    tool_use_id: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None     # full input as model emitted

    # Only on tool_result
    tool_result: ToolResultRecord | None = None

    # Only on reasoning (planner OR critic)
    thinking_blocks: list[ThinkingBlockRecord] | None = None

    # Only on input / finalize
    text: str | None = None

    # Populated on assistant-emitting steps (reasoning + tool_call cluster)
    model: str | None = None
    stop_reason: str | None = None
    usage: dict[str, int] | None = None
    # usage shape: {"input": N, "output": N, "cache_read": N, "cache_create": N}


class TrainingMetadata(BaseModel):
    """Episode-level metadata carried in trajectory JSON."""
    schema_version: str = "v2"
    planner_model: str
    critic_model: str
    image_model: str
    planner_thinking_budget: int
    critic_thinking_budget: int
    interleaved_thinking: bool
    total_input_tokens: int
    total_output_tokens: int
    total_cache_read_tokens: int = 0
    total_cache_creation_tokens: int = 0
    estimated_cost_usd: float
    wall_time_s: float
    source: Literal["agent_run", "apply_edits"] = "agent_run"
    # v2.4 Prompt Enhancer provenance — present when the pre-planner
    # stage ran. `enhancer_skipped=True` means the stage was bypassed
    # (disabled, --skip-enhancer, or the model errored); in that case
    # `original_brief` equals the top-level `brief` and the enhancer
    # token counts are 0.
    enhancer_model: str = ""
    enhancer_skipped: bool = True
    enhancer_skip_reason: str = ""
    enhancer_input_tokens: int = 0
    enhancer_output_tokens: int = 0
    enhancer_wall_time_s: float = 0.0
    # Raw user brief before enhancement (with any Template: / Attached
    # files: prologues the runner injected). Kept here so the trajectory
    # is self-describing for A/B analysis (enhancer on vs off). When
    # the enhancer was skipped, this is empty string and the top-level
    # `brief` is authoritative.
    original_brief: str = ""


class DistillTrajectory(BaseModel):
    """The on-disk training-data record for one run.

    Top-level fields contain ONLY:
      - run_id + brief (training input root)
      - agent_trace (model decisions + lean tool results)
      - episode-level reward signal (final_reward + terminal_status)
      - training metadata (model IDs, token counts, thinking config)

    Deliberately absent: design_spec, layer_graph, composition,
    critique_loop, file paths, timestamps, descriptive summaries,
    next_action hints. The product side (HTML / PSD / PPTX renders)
    lives on disk under out/runs/<run_id>/ if needed for inspection.

    Schema version: v2. NO backward compatibility with v0/v1 JSON.
    """
    run_id: str
    brief: str
    agent_trace: list[AgentTraceStep]
    final_reward: float | None = None       # = critique score on pass; None on abort/max_turns
    terminal_status: Literal["pass", "revise", "fail", "max_turns", "abort"]
    metadata: TrainingMetadata
