"""CLI entry: `longcat-design [chat|run] ...`.

Subcommands:
  chat              (default)  launch conversational REPL
  run "<brief>"     one-shot:  generate a single artifact from one brief

Examples:
  longcat-design                            # starts chat shell
  longcat-design chat                       # same
  longcat-design chat --resume <sid>        # resume existing session
  longcat-design run "a 3:4 poster for X"   # one-shot, old behavior
"""

from __future__ import annotations

import argparse
import sys

from .config import load_settings
from .runner import PipelineRunner


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="longcat-design",
        description=(
            "LongcatDesign — open-source conversational design agent. "
            "Generates editable posters, slide decks, and landing pages "
            "via chat-driven LLM orchestration."
        ),
    )
    subparsers = parser.add_subparsers(dest="subcommand", metavar="<command>")

    # chat (default)
    chat_p = subparsers.add_parser(
        "chat",
        help="launch conversational REPL (default if no subcommand given)",
    )
    chat_p.add_argument(
        "--resume",
        metavar="SESSION_ID",
        help="resume an existing session from sessions/<SESSION_ID>.json",
    )

    # run (one-shot)
    run_p = subparsers.add_parser(
        "run",
        help="one-shot: generate a single artifact from one brief",
    )
    run_p.add_argument(
        "brief",
        help="Design brief, e.g. '国宝回家 公益项目主视觉海报，竖版 3:4'",
    )

    args = parser.parse_args(argv)

    # Default subcommand = chat
    if args.subcommand is None or args.subcommand == "chat":
        from .chat import run_chat
        resume_id = getattr(args, "resume", None)
        return run_chat(resume_id=resume_id)

    if args.subcommand == "run":
        return _run_oneshot(args.brief)

    parser.print_help()
    return 1


def _run_oneshot(brief: str) -> int:
    """Legacy one-shot mode — single brief → single Trajectory on disk."""
    settings = load_settings()
    runner = PipelineRunner(settings)
    traj, traj_path = runner.run(brief)

    print()
    print(f"  Trajectory: {traj_path}")
    print(f"  PSD:        {traj.composition.psd_path}")
    print(f"  SVG:        {traj.composition.svg_path}")
    print(f"  Preview:    {traj.composition.preview_path}")
    print(f"  Layers:     {len(traj.layer_graph)}  "
          f"|  Critiques: {len(traj.critique_loop)}  "
          f"|  Trace steps: {len(traj.agent_trace)}")
    print(f"  Wall time:  {traj.metadata['wall_time_s']}s  "
          f"|  Est. cost: ${traj.metadata['estimated_cost_usd']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
