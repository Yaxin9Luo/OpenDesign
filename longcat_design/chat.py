"""Conversational CLI shell — `longcat-design chat` entry point.

Multi-turn REPL over LongcatDesign: user types a brief (or follow-up like
"make the title bigger"), agent runs full PlannerLoop, artifacts land in
`out/runs/<run_id>/`, session state persists to `sessions/<id>.json`.

Slash commands (v1.0 subset):
  :help                  show commands
  :save [id]             save session to disk (default: current session_id)
  :load <id>             replace current session with loaded one
  :new                   start fresh session (prompts to save current)
  :list                  list recent sessions (most recent first)
  :history               show message history
  :tokens  /  :cost      show cumulative stats
  :export [path]         copy all artifacts + session to path/
  :exit  /  :quit  /  :q exit (prompts save)

Anything not starting with `:` is a user brief — goes to the planner as
the next turn. Prior trajectories in the session are summarized as context
so the planner can tell "revise existing" from "make something new."
"""

from __future__ import annotations

import json
import shutil
import sys
import textwrap
import traceback
from datetime import datetime
from pathlib import Path

from .config import Settings, load_settings
from .runner import PipelineRunner
from .schema import Trajectory
from .session import (
    ChatSession,
    TrajectoryRef,
    load_session,
    list_sessions,
    new_session_id,
    save_session,
)

# Enable arrow-key history / line editing on Unix; harmless import on macOS/Linux.
try:
    import readline  # noqa: F401
except ImportError:
    pass


BANNER = (
    "LongcatDesign v0.1 — open-source conversational design agent\n"
    "Describe what you want to make, or type :help\n"
)


# --- Public entry ---------------------------------------------------------


def run_chat(resume_id: str | None = None) -> int:
    """Main chat REPL. Returns exit code."""
    try:
        settings = load_settings()
    except RuntimeError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2

    sessions_dir = settings.repo_root / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)

    if resume_id:
        try:
            session = load_session(sessions_dir, resume_id)
            print(f"\n  resumed session {session.session_id}  "
                  f"({len(session.trajectories)} prior artifact(s), "
                  f"${session.total_cost_usd()} spent)\n")
        except FileNotFoundError:
            print(f"  session not found: {resume_id}", file=sys.stderr)
            return 2
    else:
        session = ChatSession(session_id=new_session_id())
        print(f"\n{BANNER}"
              f"  session: {session.session_id}\n"
              f"  sessions dir: {sessions_dir}\n")

    state = {"session": session, "sessions_dir": sessions_dir,
             "settings": settings, "dirty": False}

    try:
        while True:
            try:
                line = input("> ").strip()
            except EOFError:
                print()
                _handle_exit(state)
                return 0
            except KeyboardInterrupt:
                print("\n  (use :exit to quit, or Ctrl-D)")
                continue

            if not line:
                continue

            if line.startswith(":"):
                should_continue = _dispatch_slash(line, state)
                if not should_continue:
                    return 0
                continue

            _handle_brief(line, state)
    except Exception as e:
        print(f"\n  fatal chat error: {e}", file=sys.stderr)
        traceback.print_exc()
        # best-effort save before exit so work isn't lost
        try:
            save_session(state["session"], state["sessions_dir"])
            print(f"  emergency-saved session {state['session'].session_id}",
                  file=sys.stderr)
        except Exception:
            pass
        return 1


# --- Brief handling (the actual work) -------------------------------------


def _handle_brief(brief: str, state: dict) -> None:
    session: ChatSession = state["session"]
    settings: Settings = state["settings"]

    # Build contextual brief: include a compact summary of prior artifacts so
    # planner can tell "revise existing" from "make new artifact."
    contextual_brief = _build_contextual_brief(brief, session)

    session.append_user(brief)

    print(f"\n  [generating — {settings.planner_model}, may take 1-5 min]\n")
    start = datetime.now()

    try:
        traj, traj_path = PipelineRunner(settings).run(contextual_brief)
    except Exception as e:
        print(f"  generation failed: {e}", file=sys.stderr)
        session.append_system(f"[error] {e}")
        state["dirty"] = True
        save_session(session, state["sessions_dir"])  # save even on failure
        return

    ref = _trajectory_to_ref(traj, traj_path)
    session.trajectories.append(ref)
    session.current_artifact_type = traj.design_spec.artifact_type
    session.append_assistant(
        _assistant_summary(traj, ref),
        trajectory_id=traj.run_id,
    )
    state["dirty"] = False  # we auto-save after each turn
    save_session(session, state["sessions_dir"])

    elapsed = (datetime.now() - start).total_seconds()
    _display_turn_result(traj, ref, elapsed, session)


def _build_contextual_brief(user_text: str, session: ChatSession) -> str:
    """Prepend session context for the planner when prior artifacts exist."""
    if not session.trajectories:
        return user_text

    # Summarize the most recent artifact so the planner has something to
    # revise-vs-recreate. Don't dump all prior — planner's context budget
    # stays lean; session file has the full history.
    latest = session.trajectories[-1]
    latest_summary = (
        f"## Prior artifact in this chat session\n"
        f"- type: {latest.artifact_type.value}\n"
        f"- run_id: {latest.run_id}\n"
        f"- n_layers: {latest.n_layers}, critic: {latest.verdict} ({latest.score})\n"
        f"- trajectory: {latest.trajectory_path}\n"
        f"\n"
        f"The user's next request may be:\n"
        f"  (a) a REVISION to this prior artifact (e.g. 'make title bigger', "
        f"'try a red palette') — re-call propose_design_spec with revisions, "
        f"re-render only affected layers, recomposite.\n"
        f"  (b) a NEW artifact, possibly of a different type (e.g. 'now make "
        f"a landing page') — call switch_artifact_type first, then propose_design_spec.\n"
        f"Decide based on the user's intent below.\n\n"
        f"## User's next request\n"
        f"{user_text}"
    )
    return latest_summary


# --- Slash command dispatch -----------------------------------------------


def _dispatch_slash(line: str, state: dict) -> bool:
    """Returns True to continue REPL, False to exit."""
    parts = line[1:].split(maxsplit=1)
    cmd = parts[0].lower() if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else ""

    handlers = {
        "help":    _cmd_help,
        "h":       _cmd_help,
        "?":       _cmd_help,
        "save":    _cmd_save,
        "load":    _cmd_load,
        "new":     _cmd_new,
        "list":    _cmd_list,
        "ls":      _cmd_list,
        "history": _cmd_history,
        "tokens":  _cmd_tokens,
        "cost":    _cmd_tokens,
        "export":  _cmd_export,
        "exit":    _cmd_exit,
        "quit":    _cmd_exit,
        "q":       _cmd_exit,
    }
    handler = handlers.get(cmd)
    if handler is None:
        print(f"  unknown command :{cmd}. Type :help.")
        return True
    return handler(arg, state)


# --- Slash command handlers -----------------------------------------------


def _cmd_help(arg: str, state: dict) -> bool:
    print(textwrap.dedent("""
        Commands:
          :help / :h / :?       show this
          :save [id]            save session (default: current session_id)
          :load <id>            replace current session with loaded one
          :new                  start fresh session (auto-saves current)
          :list / :ls           list recent sessions
          :history              show message history
          :tokens / :cost       cumulative tokens + cost for this session
          :export [path]        copy artifacts + session to path (default: ~/Desktop/<id>)
          :exit / :quit / :q    exit (auto-saves)

        Anything not starting with ':' is a brief — sent to the agent
        as the next design turn. Prior artifacts in this session get
        summarized as context so the agent can revise vs create fresh.
    """).strip())
    return True


def _cmd_save(arg: str, state: dict) -> bool:
    session: ChatSession = state["session"]
    if arg:
        session.session_id = arg
    path = save_session(session, state["sessions_dir"])
    state["dirty"] = False
    print(f"  saved: {path}")
    return True


def _cmd_load(arg: str, state: dict) -> bool:
    if not arg:
        print("  usage: :load <session_id>")
        return True
    try:
        new = load_session(state["sessions_dir"], arg)
    except FileNotFoundError:
        print(f"  session not found: {arg}")
        return True
    if state["dirty"]:
        save_session(state["session"], state["sessions_dir"])
        print(f"  (auto-saved previous session {state['session'].session_id})")
    state["session"] = new
    print(f"  loaded {new.session_id}  ({len(new.trajectories)} artifact(s))")
    return True


def _cmd_new(arg: str, state: dict) -> bool:
    # Auto-save current session
    save_session(state["session"], state["sessions_dir"])
    print(f"  (auto-saved previous session {state['session'].session_id})")
    state["session"] = ChatSession(session_id=new_session_id())
    state["dirty"] = False
    print(f"  new session: {state['session'].session_id}")
    return True


def _cmd_list(arg: str, state: dict) -> bool:
    items = list_sessions(state["sessions_dir"])
    if not items:
        print("  (no sessions in this dir)")
        return True
    print(f"  recent sessions ({len(items)}):")
    for sid, updated, n_traj in items:
        marker = "*" if sid == state["session"].session_id else " "
        print(f"    {marker} {sid}  {updated.strftime('%Y-%m-%d %H:%M')}  {n_traj} artifact(s)")
    return True


def _cmd_history(arg: str, state: dict) -> bool:
    session: ChatSession = state["session"]
    if not session.message_history:
        print("  (no messages yet)")
        return True
    print(f"  session: {session.session_id}")
    for i, msg in enumerate(session.message_history, 1):
        stamp = msg.timestamp.strftime("%H:%M:%S")
        tag = f"[{msg.role}]"
        body = msg.content if len(msg.content) <= 200 else msg.content[:197] + "..."
        prefix = f"    {i:3d} {stamp} {tag:12s}"
        print(f"{prefix} {body}")
        if msg.trajectory_id:
            print(f"        {' ' * 21}→ trajectory {msg.trajectory_id}")
    return True


def _cmd_tokens(arg: str, state: dict) -> bool:
    session: ChatSession = state["session"]
    if not session.trajectories:
        print("  no artifacts generated yet")
        return True
    print(f"  session: {session.session_id}")
    print(f"  artifacts:   {len(session.trajectories)}")
    print(f"  total cost:  ${session.total_cost_usd()}")
    print(f"  total wall:  {session.total_wall_s()}s")
    print(f"  per-artifact:")
    for i, t in enumerate(session.trajectories, 1):
        print(f"    [{i}] {t.artifact_type.value}  "
              f"{t.n_layers} layers  "
              f"{t.verdict}({t.score})  "
              f"${t.cost_usd}  "
              f"{t.wall_s}s  "
              f"run_id={t.run_id}")
    return True


def _cmd_export(arg: str, state: dict) -> bool:
    session: ChatSession = state["session"]
    if not session.trajectories:
        print("  nothing to export — no artifacts in this session")
        return True
    dest = Path(arg).expanduser() if arg else Path.home() / "Desktop" / session.session_id
    dest.mkdir(parents=True, exist_ok=True)

    # Save a fresh copy of the session JSON into dest
    session_path_new = dest / f"{session.session_id}.json"
    with open(session_path_new, "w", encoding="utf-8") as f:
        payload = session.model_dump(mode="json")
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    print(f"  session → {session_path_new}")

    # Copy each artifact's run_dir content
    copied = 0
    for ref in session.trajectories:
        src_run_dir = Path(ref.trajectory_path).parent.parent / "runs" / ref.run_id
        if not src_run_dir.exists():
            print(f"  (skipped missing run dir: {src_run_dir})")
            continue
        dst_dir = dest / ref.run_id
        if dst_dir.exists():
            shutil.rmtree(dst_dir)
        shutil.copytree(src_run_dir, dst_dir)
        # Also copy the trajectory JSON
        traj_src = Path(ref.trajectory_path)
        if traj_src.exists():
            shutil.copy2(traj_src, dst_dir / "trajectory.json")
        copied += 1
    print(f"  copied {copied} artifact(s) → {dest}")
    return True


def _cmd_exit(arg: str, state: dict) -> bool:
    _handle_exit(state)
    return False


def _handle_exit(state: dict) -> None:
    session: ChatSession = state["session"]
    path = save_session(session, state["sessions_dir"])
    summary = (
        f"  saved {path.name}  "
        f"({len(session.trajectories)} artifact(s), "
        f"${session.total_cost_usd()}, "
        f"{session.total_wall_s()}s)"
    )
    print(summary)
    print("  bye.")


# --- Presentation ---------------------------------------------------------


def _trajectory_to_ref(traj: Trajectory, traj_path: Path) -> TrajectoryRef:
    latest_critique = traj.critique_loop[-1] if traj.critique_loop else None
    return TrajectoryRef(
        run_id=traj.run_id,
        artifact_type=traj.design_spec.artifact_type,
        created_at=traj.created_at,
        trajectory_path=str(traj_path),
        preview_path=traj.composition.preview_path,
        psd_path=traj.composition.psd_path,
        svg_path=traj.composition.svg_path,
        n_layers=len(traj.layer_graph),
        verdict=latest_critique.verdict if latest_critique else None,
        score=latest_critique.score if latest_critique else None,
        cost_usd=float(traj.metadata.get("estimated_cost_usd", 0.0)),
        wall_s=float(traj.metadata.get("wall_time_s", 0.0)),
    )


def _assistant_summary(traj: Trajectory, ref: TrajectoryRef) -> str:
    """User-facing one-line summary (goes into message_history as content)."""
    verdict_str = f"{ref.verdict}({ref.score:.2f})" if ref.verdict else "no critique"
    return (
        f"produced {ref.artifact_type.value} · {ref.n_layers} layers · "
        f"{verdict_str} · ${ref.cost_usd} · {ref.wall_s}s · "
        f"run_id={ref.run_id}"
    )


def _display_turn_result(traj: Trajectory, ref: TrajectoryRef,
                         elapsed: float, session: ChatSession) -> None:
    verdict_str = f"{ref.verdict} ({ref.score:.2f})" if ref.verdict else "no critique"
    print(f"\n  ✓ {ref.artifact_type.value} generated")
    print(f"    layers:     {ref.n_layers}")
    print(f"    critique:   {verdict_str}")
    print(f"    cost:       ${ref.cost_usd}  ({ref.wall_s}s wall)")
    print(f"    preview:    {ref.preview_path}")
    if ref.psd_path:
        print(f"    PSD:        {ref.psd_path}")
    if ref.svg_path:
        print(f"    SVG:        {ref.svg_path}")
    print(f"    trajectory: {ref.trajectory_path}")
    print(f"  session total: {len(session.trajectories)} artifact(s), "
          f"${session.total_cost_usd()}, {session.total_wall_s()}s")
    print()
