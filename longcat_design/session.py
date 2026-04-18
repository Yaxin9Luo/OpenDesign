"""ChatSession — outer container for multi-turn conversational design.

A `ChatSession` wraps N `Trajectory` artifacts produced across the session's
turns. Each user brief (non-slash input) → full PlannerLoop → one Trajectory
appended to `trajectories`. Slash commands mutate session state without
producing a trajectory.

Session state persists to `sessions/<session_id>.json`. Artifacts (PSD/SVG/
PNG) still live under `out/runs/<run_id>/`; the session file stores only
metadata + refs to those artifacts' trajectory JSONs.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from .schema import ArtifactType
from .util.io import atomic_write_json


SESSION_SCHEMA_VERSION = "v1.0-chat"


class ChatMessage(BaseModel):
    """One turn of the user↔assistant conversation (not the full agent trace).

    Multiple assistant actions (multiple tool calls inside PlannerLoop) collapse
    into ONE ChatMessage with role=assistant and a user-facing `content`
    summary. The full per-tool trace lives in the linked Trajectory.
    """
    role: Literal["user", "assistant", "system"]
    content: str
    timestamp: datetime = Field(default_factory=datetime.now)
    trajectory_id: str | None = None  # set on assistant msgs that produced an artifact


class TrajectoryRef(BaseModel):
    """Lightweight pointer from session → full Trajectory on disk."""
    run_id: str                                # matches Trajectory.run_id
    artifact_type: ArtifactType
    created_at: datetime
    trajectory_path: str                       # absolute path to trajectory.json
    preview_path: str                          # absolute path to flattened preview
    psd_path: str | None = None
    svg_path: str | None = None
    html_path: str | None = None
    pptx_path: str | None = None
    n_layers: int
    verdict: Literal["pass", "revise", "fail"] | None = None
    score: float | None = None
    cost_usd: float = 0.0
    wall_s: float = 0.0


class ChatSession(BaseModel):
    """Persistent conversation state across N artifact generations."""
    session_id: str
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    current_artifact_type: ArtifactType = ArtifactType.POSTER
    message_history: list[ChatMessage] = Field(default_factory=list)
    trajectories: list[TrajectoryRef] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    # ---- ergonomic helpers -------------------------------------------------

    def append_user(self, content: str) -> ChatMessage:
        msg = ChatMessage(role="user", content=content)
        self.message_history.append(msg)
        self.updated_at = datetime.now()
        return msg

    def append_assistant(self, content: str, trajectory_id: str | None = None) -> ChatMessage:
        msg = ChatMessage(role="assistant", content=content, trajectory_id=trajectory_id)
        self.message_history.append(msg)
        self.updated_at = datetime.now()
        return msg

    def append_system(self, content: str) -> ChatMessage:
        msg = ChatMessage(role="system", content=content)
        self.message_history.append(msg)
        self.updated_at = datetime.now()
        return msg

    def latest_trajectory(self) -> TrajectoryRef | None:
        return self.trajectories[-1] if self.trajectories else None

    def total_cost_usd(self) -> float:
        return round(sum(t.cost_usd for t in self.trajectories), 4)

    def total_wall_s(self) -> float:
        return round(sum(t.wall_s for t in self.trajectories), 2)


# ---- Persistence --------------------------------------------------------


def new_session_id() -> str:
    """Sortable session id: YYYYMMDD-HHMMSS-shortuuid."""
    import uuid
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"session_{ts}_{uuid.uuid4().hex[:8]}"


def session_path(sessions_dir: Path, session_id: str) -> Path:
    return sessions_dir / f"{session_id}.json"


def save_session(session: ChatSession, sessions_dir: Path) -> Path:
    path = session_path(sessions_dir, session.session_id)
    payload = session.model_dump(mode="json")
    payload["_schema_version"] = SESSION_SCHEMA_VERSION
    atomic_write_json(path, payload)
    return path


def load_session(sessions_dir: Path, session_id: str) -> ChatSession:
    path = session_path(sessions_dir, session_id)
    if not path.exists():
        raise FileNotFoundError(f"session not found: {path}")
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    payload.pop("_schema_version", None)
    try:
        return ChatSession.model_validate(payload)
    except ValidationError as e:
        raise RuntimeError(
            f"session {session_id} failed schema validation: "
            f"{e.errors(include_url=False)[:3]}"
        ) from e


def list_sessions(sessions_dir: Path, limit: int = 20) -> list[tuple[str, datetime, int]]:
    """Return (session_id, updated_at, n_trajectories) for recent sessions, newest first."""
    if not sessions_dir.exists():
        return []
    items: list[tuple[str, datetime, int]] = []
    for p in sessions_dir.glob("session_*.json"):
        try:
            with open(p, encoding="utf-8") as f:
                raw = json.load(f)
            items.append((
                raw["session_id"],
                datetime.fromisoformat(raw["updated_at"]),
                len(raw.get("trajectories", [])),
            ))
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    items.sort(key=lambda x: x[1], reverse=True)
    return items[:limit]
