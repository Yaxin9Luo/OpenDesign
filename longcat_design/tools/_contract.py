"""Tool-handler contract — every tool returns a ToolObservation.

Shape lives in schema.py; this module exposes the type + ergonomic constructors
so tool implementations stay short.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol

from ..schema import ToolObservation


class ToolHandler(Protocol):
    """Signature every registered tool implements."""
    def __call__(self, args: dict[str, Any], *, ctx: "ToolContext") -> ToolObservation: ...


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
            "composition": None,            # CompositionArtifacts after composite
            "critique_results": [],         # list[CritiqueResult]
            "finalized": False,
        }


def obs_ok(summary: str, **kw: Any) -> ToolObservation:
    return ToolObservation(status="ok", summary=summary, **kw)


def obs_error(summary: str, **kw: Any) -> ToolObservation:
    return ToolObservation(status="error", summary=summary, **kw)


def obs_partial(summary: str, **kw: Any) -> ToolObservation:
    return ToolObservation(status="partial", summary=summary, **kw)


def obs_not_found(summary: str, **kw: Any) -> ToolObservation:
    return ToolObservation(status="not_found", summary=summary, **kw)
