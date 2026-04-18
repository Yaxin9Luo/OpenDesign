"""CLI entry: `python -m longcat_design.cli "<brief>"` or `longcat-design "<brief>"`."""

from __future__ import annotations

import argparse
import sys

from .config import load_settings
from .runner import PipelineRunner


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="longcat-design",
        description="Layered poster generation: brief → editable PSD + SVG + trajectory.json",
    )
    parser.add_argument("brief", help="Design brief, e.g. '国宝回家 公益项目主视觉海报，竖版 3:4'")
    args = parser.parse_args(argv)

    settings = load_settings()
    runner = PipelineRunner(settings)
    traj, traj_path = runner.run(args.brief)

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
