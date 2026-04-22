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
    run_p.add_argument(
        "--template",
        metavar="NAME",
        default=None,
        help=("Poster canvas preset — e.g. 'neurips-portrait' "
              "(1536×2048 @300dpi), 'cvpr-landscape', 'icml-portrait', "
              "'a0-portrait', 'a0-landscape'. When set, the resolved "
              "canvas (w_px / h_px / dpi / aspect_ratio) is injected "
              "into the brief prologue so the planner uses it."),
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
        return _run_oneshot(
            args.brief,
            getattr(args, "from_file", []) or [],
            template=getattr(args, "template", None),
        )

    if args.subcommand == "apply-edits":
        return _run_apply_edits(args.html, args.out_dir)

    parser.print_help()
    return 1


def _run_oneshot(brief: str, from_file: list[str],
                 *, template: str | None = None) -> int:
    """One-shot mode — single brief (+ optional attachments) → single trajectory."""
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

    # v2.3 — validate template name early so a typo fails before any API cost.
    if template is not None:
        from .config import resolve_template, available_templates
        if resolve_template(template) is None:
            names = " / ".join(available_templates())
            print(f"  error: unknown template {template!r}. "
                  f"Available: {names}", file=sys.stderr)
            return 2

    settings = load_settings()
    runner = PipelineRunner(settings)
    traj, traj_path = runner.run(brief, attachments=attachments,
                                  template=template)

    # v2 trajectory carries no product paths. Locate run dir from the
    # trajectory file's sibling structure. v2.2 versioning: composites
    # live under run_dir/composites/iter_<N>/; the final/ subdirectory
    # has symlinks pointing at the latest iteration.
    run_dir = traj_path.parent.parent / "runs" / traj.run_id
    final_dir = run_dir / "final"
    n_layers = _count_unique_layers_from_trace(traj.agent_trace)
    n_critiques = _count_critiques_from_trace(traj.agent_trace)

    print()
    if attachments:
        print(f"  Ingested:        {', '.join(str(p.name) for p in attachments)}")
    print(f"  Trajectory:      {traj_path}")
    print(f"  Run dir:         {run_dir}")
    for fname in ("preview.png", "poster.psd", "poster.svg",
                  "poster.html", "index.html", "deck.pptx"):
        fp = final_dir / fname
        if fp.exists():
            print(f"  {fname:14s}:  {fp}")
    print(f"  Layers:          {n_layers}  "
          f"|  Critiques: {n_critiques}  "
          f"|  Trace steps: {len(traj.agent_trace)}")
    print(f"  Terminal:        {traj.terminal_status}  "
          f"|  Reward: {traj.final_reward}")
    print(f"  Wall time:       {traj.metadata.wall_time_s}s  "
          f"|  Est. cost: ${traj.metadata.estimated_cost_usd}")
    return 0


def _count_unique_layers_from_trace(trace) -> int:
    """Count distinct layer_ids the planner created via render/generate/edit
    tool_calls. A v2-trajectory replacement for `len(traj.layer_graph)`."""
    seen: set[str] = set()
    rendering_tools = {
        "render_text_layer", "generate_background", "generate_image",
        "edit_layer",
    }
    for s in trace:
        if s.type == "tool_call" and s.tool_name in rendering_tools:
            args = s.tool_args or {}
            lid = args.get("layer_id")
            if lid:
                seen.add(lid)
    return len(seen)


def _count_critiques_from_trace(trace) -> int:
    return sum(1 for s in trace
               if s.type == "tool_call" and s.tool_name == "critique")


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
        traj, traj_path, run_dir, restored_layer_ids, skipped = apply_edits(
            src, settings=settings, out_dir=out,
        )
    except (ValueError, RuntimeError, FileNotFoundError) as e:
        print(f"  error: {e}", file=sys.stderr)
        return 2

    print()
    print(f"  Source HTML:     {src}")
    print(f"  New run_id:      {traj.run_id}")
    print(f"  Trajectory:      {traj_path}")
    print(f"  Run dir:         {run_dir}")
    final_dir = run_dir / "final"
    for fname in ("preview.png", "poster.psd", "poster.svg",
                  "poster.html", "index.html"):
        fp = final_dir / fname
        if fp.exists():
            print(f"  {fname:14s}:  {fp}")
    print(f"  Layers restored: {len(restored_layer_ids)}"
          + (f"  |  skipped: {len(skipped)} ({', '.join(skipped)})"
             if skipped else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
