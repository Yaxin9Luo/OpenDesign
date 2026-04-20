"""CLI entry: `longcat-design [chat|run|apply-edits] ...`.

Subcommands:
  chat                     (default) launch conversational REPL
  run "<brief>"            one-shot: generate a single artifact from one brief
  apply-edits <html>       round-trip an edited poster HTML → new PSD/SVG/HTML/PNG

Examples:
  longcat-design                             # starts chat shell
  longcat-design chat                        # same
  longcat-design chat --resume <sid>         # resume existing session
  longcat-design run "a 3:4 poster for X"    # one-shot, old behavior
  longcat-design apply-edits ~/poster.edited.html  # re-render from edits
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

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
    run_p.add_argument(
        "--from-file",
        metavar="PATH",
        action="append",
        default=[],
        help=("Attach a source document to this brief (PDF / Markdown / "
              "image). Repeatable. Planner will call `ingest_document` "
              "on these files FIRST, then use the extracted structure "
              "(title / sections / figures) to drive the DesignSpec."),
    )

    # apply-edits (round-trip edited HTML)
    ae_p = subparsers.add_parser(
        "apply-edits",
        help=("round-trip an edited poster HTML back into a fresh "
              "PSD/SVG/HTML/preview set (+ trajectory)"),
    )
    ae_p.add_argument(
        "html",
        help="Path to the edited HTML (e.g. ~/Downloads/poster.edited.html)",
    )
    ae_p.add_argument(
        "-o", "--out-dir",
        metavar="PATH",
        help=("Output run directory (default: out/runs/<new-run-id>). "
              "The new run is always self-contained."),
    )

    args = parser.parse_args(argv)

    # Default subcommand = chat
    if args.subcommand is None or args.subcommand == "chat":
        from .chat import run_chat
        resume_id = getattr(args, "resume", None)
        return run_chat(resume_id=resume_id)

    if args.subcommand == "run":
        return _run_oneshot(args.brief, getattr(args, "from_file", []) or [])

    if args.subcommand == "apply-edits":
        return _run_apply_edits(args.html, args.out_dir)

    parser.print_help()
    return 1


def _run_oneshot(brief: str, from_file: list[str]) -> int:
    """One-shot mode — single brief (+ optional attachments) → single Trajectory."""
    # v1.1: resolve --from-file attachments into Path objects and validate early.
    attachments: list[Path] = []
    for fp_str in from_file:
        p = Path(fp_str).expanduser().resolve()
        if not p.exists():
            print(f"  error: attachment not found: {p}", file=sys.stderr)
            return 2
        if not p.is_file():
            print(f"  error: attachment not a file: {p}", file=sys.stderr)
            return 2
        attachments.append(p)

    settings = load_settings()
    runner = PipelineRunner(settings)
    traj, traj_path = runner.run(brief, attachments=attachments)

    print()
    if attachments:
        print(f"  Ingested:   {', '.join(str(p.name) for p in attachments)}")
    print(f"  Trajectory: {traj_path}")
    print(f"  PSD:        {traj.composition.psd_path}")
    print(f"  SVG:        {traj.composition.svg_path}")
    if traj.composition.html_path:
        print(f"  HTML:       {traj.composition.html_path}")
    if traj.composition.pptx_path:
        print(f"  PPTX:       {traj.composition.pptx_path}")
    print(f"  Preview:    {traj.composition.preview_path}")
    print(f"  Layers:     {len(traj.layer_graph)}  "
          f"|  Critiques: {len(traj.critique_loop)}  "
          f"|  Trace steps: {len(traj.agent_trace)}")
    print(f"  Wall time:  {traj.metadata['wall_time_s']}s  "
          f"|  Est. cost: ${traj.metadata['estimated_cost_usd']}")
    return 0


def _run_apply_edits(html_path: str, out_dir: str | None) -> int:
    """Round-trip edited HTML → new render_dir + trajectory."""
    from .apply_edits import apply_edits

    src = Path(html_path).expanduser().resolve()
    if not src.exists():
        print(f"  error: edited HTML not found: {src}", file=sys.stderr)
        return 2
    out = Path(out_dir).expanduser().resolve() if out_dir else None

    settings = load_settings()
    try:
        traj, traj_path = apply_edits(src, settings=settings, out_dir=out)
    except (ValueError, RuntimeError, FileNotFoundError) as e:
        print(f"  error: {e}", file=sys.stderr)
        return 2

    parent = traj.metadata.get("parent_run_id") or "(none)"
    skipped = traj.metadata.get("skipped_layers") or []
    print()
    print(f"  Source HTML:     {src}")
    print(f"  Parent run:      {parent}")
    print(f"  New run_id:      {traj.run_id}")
    print(f"  Trajectory:      {traj_path}")
    print(f"  PSD:             {traj.composition.psd_path}")
    print(f"  SVG:             {traj.composition.svg_path}")
    if traj.composition.html_path:
        print(f"  HTML:            {traj.composition.html_path}")
    print(f"  Preview:         {traj.composition.preview_path}")
    print(f"  Layers restored: {len(traj.layer_graph)}"
          + (f"  |  skipped: {len(skipped)} ({', '.join(skipped)})"
             if skipped else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
