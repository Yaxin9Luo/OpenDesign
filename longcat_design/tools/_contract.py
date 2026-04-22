"""Tool-handler contract — every tool returns a ToolResultRecord.

Shape lives in schema.py; this module exposes the type + ergonomic
constructors so tool implementations stay short.

v2 (training-data capture, 2026-04-22):
- Returns ToolResultRecord (replaces ToolObservation).
- `obs_ok(payload=...)` — payload is the lean state the policy needs to
  act on next turn (IDs / sha256 / minimal data). NO descriptive summary,
  NO next_actions hints, NO file paths. Hints would leak into the policy
  and cause shortcut learning at deploy time. The workflow contract in
  prompts/planner.md is the single source of "what to do next."
- `obs_error(message, category=...)` — full error message preserved
  (no truncation) so the policy can learn recovery; typed category enum
  lets reward models distinguish model-side from environment-side errors.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol

from ..schema import ErrorCategory, ToolResultRecord


class ToolHandler(Protocol):
    """Signature every registered tool implements."""
    def __call__(self, args: dict[str, Any], *, ctx: "ToolContext") -> ToolResultRecord: ...


class ToolContext:
    """Mutable per-run context handed to every tool call.

    Holds paths, the Settings object, and a dict where tools can stash
    artifacts the runner needs to read after the loop ends (background path,
    layer manifests, the latest DesignSpec, etc.).
    """

    def __init__(self, *, settings, run_dir, layers_dir, run_id):
        self.settings = settings
        self.run_dir = run_dir
        self.layers_dir = layers_dir
        self.run_id = run_id
        self.state: dict[str, Any] = {
            "artifact_type": "poster",      # set by switch_artifact_type; default=poster
            "design_spec": None,            # populated by propose_design_spec
            "rendered_layers": {},          # layer_id -> {png_path, name, kind, bbox, ...}
            "composition": None,            # CompositionArtifacts after composite (runtime only)
            "critique_results": [],         # list[CritiqueResult]
            "finalized": False,
        }


def obs_ok(payload: dict[str, Any] | None = None) -> ToolResultRecord:
    """Success path. payload should be the *minimum* state the policy needs:
    IDs the next tool_call must reference, sha256 of artifacts so the policy
    can verify its action produced a unique output, and for critique the full
    CritiqueResult dump."""
    return ToolResultRecord(status="ok", payload=payload or {})


def obs_error(
    message: str,
    category: ErrorCategory = "unknown",
    payload: dict[str, Any] | None = None,
) -> ToolResultRecord:
    """Error path. message is preserved in full (NO truncation) so the policy
    can learn recovery from concrete error text. category is a typed enum so
    reward models can tell environment errors (api/safety_filter) apart from
    model errors (validation). payload may carry minimal diagnostic state."""
    return ToolResultRecord(
        status="error",
        error_message=message,
        error_category=category,
        payload=payload or {},
    )
