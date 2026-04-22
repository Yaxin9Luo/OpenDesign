"""Export DistillTrajectory JSON files into OpenAI-compat SFT jsonl.

Each v2 DistillTrajectory in `out/trajectories/*.json` becomes **one jsonl
record per assistant turn** — this is the standard SFT training-data shape
(input = conversation-so-far, target = this turn's reasoning + tool_calls).
Each record is self-contained: system prompt + full message history up to
the turn, the target output, the available tools, plus episode-level
reward/metadata for filtering and RL post-training.

Output format (one JSON object per line):

    {
      "run_id": "...", "turn_idx": 3, "actor": "planner" | "critic",
      "model": "...", "provider": "anthropic" | "openai_compat",
      "messages": [                         // OpenAI Chat Completions shape
        {"role": "system", "content": "..."},
        {"role": "user",   "content": "..."},
        {"role": "assistant", "reasoning_content": "...",
         "tool_calls": [{"id": "...", "type": "function",
                         "function": {"name": "...", "arguments": "..."}}]},
        {"role": "tool", "tool_call_id": "...", "content": "..."},
        ...
      ],
      "target": {
        "reasoning_content": "...",          // this turn's CoT, may be empty
        "content": null | "...",             // planner assistant text (rare)
        "tool_calls": [...] | null           // null on final "end_turn"
      },
      "tools": [...],                         // OpenAI function shape
      "metadata": {
        "terminal_status": "pass" | "revise" | ...,
        "final_reward": 0.89,
        "turn_stop_reason": "tool_use" | "end_turn" | ...,
        "turn_usage": {"input":N, "output":N, "cache_read":N, "cache_create":N},
        "thinking_budget": 10000,
        "trajectory_source": "agent_run" | "apply_edits"
      }
    }

Critic turns emit records with actor="critic" — they target verdict JSON,
not tool_calls. The "messages" history for critic records is minimal:
system = critic rubric, user = one vision/text prompt with the preview/spec.
In the current system the critic is stateless per-critique so one record
per critic call is enough (no multi-turn history).

Usage:
    uv run python scripts/export_sft_jsonl.py \\
        --trajectories out/trajectories \\
        --out dataset/sft.jsonl \\
        [--min-reward 0.7] [--source agent_run] [--actor planner] \\
        [--provider openai_compat]
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path
from typing import Any, Iterable

# Add repo root to path so `longcat_design` imports work when running this
# script directly.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from longcat_design.schema import DistillTrajectory  # noqa: E402
from longcat_design.tools import TOOL_SCHEMAS  # noqa: E402


def _load_planner_system_prompt() -> str:
    path = REPO_ROOT / "prompts" / "planner.md"
    return path.read_text(encoding="utf-8")


def _load_critic_system_prompt(artifact_type: str) -> str:
    # Heuristic: pick the same prompt the runtime critic uses per artifact_type.
    filename = {
        "poster": "critic.md",
        "landing": "critic-landing.md",
        "deck": "critic-deck.md",
    }.get(artifact_type, "critic.md")
    return (REPO_ROOT / "prompts" / filename).read_text(encoding="utf-8")


def _tool_schemas_openai() -> list[dict[str, Any]]:
    """Translate our Anthropic-shape TOOL_SCHEMAS into OpenAI function shape."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t["input_schema"],
            },
        }
        for t in TOOL_SCHEMAS
    ]


def _detect_provider(model: str) -> str:
    m = (model or "").lower()
    if m.startswith("anthropic/") or m.startswith("claude-"):
        return "anthropic"
    return "openai_compat"


def _tool_calls_from_cluster(
    cluster: list[Any], tool_results_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Given a cluster of consecutive tool_call steps, build the OpenAI-shape
    `tool_calls` field for the assistant message."""
    out: list[dict[str, Any]] = []
    for step in cluster:
        out.append({
            "id": step.tool_use_id or "",
            "type": "function",
            "function": {
                "name": step.tool_name or "",
                "arguments": json.dumps(step.tool_args or {}, ensure_ascii=False),
            },
        })
    return out


def _serialize_tool_result(step: Any) -> str:
    """Tool results go back to the model as the tool message `content`. We
    serialize the lean ToolResultRecord payload (status + error + payload) as
    JSON so the policy sees the same structured signal it saw at runtime."""
    if step.tool_result is None:
        return "{}"
    return json.dumps(step.tool_result.model_dump(mode="json"), ensure_ascii=False)


def _walk_planner_turns(
    traj: DistillTrajectory,
    system_prompt: str,
    tools: list[dict[str, Any]],
) -> Iterable[dict[str, Any]]:
    """Yield one SFT record per planner-assistant turn.

    A "turn" here = one model call from the planner's POV: optionally a
    `reasoning` step, then zero or more `tool_call` steps (parallel tool use
    is one turn), then the corresponding `tool_result` steps close out.
    Each turn's record: history-so-far as `messages`, this turn's (reasoning
    + tool_calls) as `target`.
    """
    brief = traj.brief
    provider = _detect_provider(traj.metadata.planner_model)

    # Running `messages` list — grows as we replay the trace.
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": brief},
    ]

    steps = traj.agent_trace
    i = 0
    turn_idx = 0
    while i < len(steps):
        step = steps[i]

        # Skip non-planner-turn markers (`input` is handled above; `finalize`
        # is terminal and has no target).
        if step.type == "input" or step.type == "finalize":
            i += 1
            continue

        # Planner turn detection: a planner turn starts with EITHER a
        # `reasoning` step (with actor="planner") OR a `tool_call` step (if
        # the model skipped thinking on that turn). Critic-side `reasoning`
        # steps are actor="critic" and get routed separately below.
        if step.actor == "critic" and step.type == "reasoning":
            # Critic reasoning — not a planner turn. Skip here (handled in
            # _critic_turns pass). The critic's reasoning blocks also don't
            # affect planner message history.
            i += 1
            continue

        if step.type not in ("reasoning", "tool_call"):
            i += 1
            continue

        # Collect the full planner turn: optional reasoning, then a cluster
        # of tool_calls (until next `tool_result` starts for that cluster).
        turn_reasoning_text = ""
        if step.type == "reasoning":
            if step.thinking_blocks:
                turn_reasoning_text = "\n\n".join(
                    b.thinking for b in step.thinking_blocks if not b.is_redacted and b.thinking
                )
            stop_reason = step.stop_reason
            turn_usage = step.usage or {}
            turn_model = step.model or traj.metadata.planner_model
            i += 1
        else:
            stop_reason = None
            turn_usage = {}
            turn_model = traj.metadata.planner_model

        # Collect consecutive tool_call steps (parallel tool use).
        tool_call_cluster: list[Any] = []
        while i < len(steps) and steps[i].type == "tool_call" and steps[i].actor == "planner":
            tool_call_cluster.append(steps[i])
            i += 1

        # Collect the matching tool_result steps (paired by tool_use_id).
        tool_result_cluster: list[Any] = []
        while i < len(steps) and steps[i].type == "tool_result":
            tool_result_cluster.append(steps[i])
            i += 1

        # Also skip any inter-cluster critic reasoning (emitted after critique
        # tool_result) — it adds history for the planner's next turn via the
        # tool_result content, not via assistant messages of its own.
        while i < len(steps) and steps[i].actor == "critic" and steps[i].type == "reasoning":
            i += 1

        # Build the target (this turn's output).
        tool_calls_field = _tool_calls_from_cluster(tool_call_cluster, {}) if tool_call_cluster else None
        target = {
            "reasoning_content": turn_reasoning_text,
            "content": None,  # Our planner rarely emits standalone text; tool_calls dominate
            "tool_calls": tool_calls_field,
        }

        yield {
            "run_id": traj.run_id,
            "turn_idx": turn_idx,
            "actor": "planner",
            "model": turn_model,
            "provider": provider,
            "messages": [dict(m) for m in messages],
            "target": target,
            "tools": tools,
            "metadata": {
                "terminal_status": traj.terminal_status,
                "final_reward": traj.final_reward,
                "turn_stop_reason": stop_reason,
                "turn_usage": turn_usage,
                "thinking_budget": traj.metadata.planner_thinking_budget,
                "trajectory_source": traj.metadata.source,
            },
        }
        turn_idx += 1

        # Now append THIS turn's assistant + tool results onto `messages`
        # so the NEXT record's history is correct.
        assistant_msg: dict[str, Any] = {"role": "assistant"}
        if turn_reasoning_text:
            assistant_msg["reasoning_content"] = turn_reasoning_text
        if tool_calls_field:
            assistant_msg["tool_calls"] = tool_calls_field
        messages.append(assistant_msg)
        for tr_step in tool_result_cluster:
            messages.append({
                "role": "tool",
                "tool_call_id": tr_step.tool_use_id or "",
                "content": _serialize_tool_result(tr_step),
            })


def _walk_critic_turns(traj: DistillTrajectory) -> Iterable[dict[str, Any]]:
    """Yield one SFT record per critic call.

    Critic is stateless per-call in our system: one system prompt (rubric
    for the artifact type) + one user message (flattened preview or
    spec-summary) → one output (verdict JSON + reasoning). We recover the
    target from the `critique` tool_result payload (full CritiqueResult dump)
    and the critic's own `reasoning` step that follows it in the trace.

    Note: the critic's `user` message shape was built at runtime in
    critic.py by _build_user_text / _build_landing_user_text /
    _build_deck_user_text. We DON'T re-serialize it here because the
    vision image (for poster) is large + host-path dependent. Instead we
    record a placeholder pointer to `out/runs/<run_id>/final/preview.png`
    so the SFT trainer can reconstruct the image block if needed.
    """
    provider = _detect_provider(traj.metadata.critic_model)
    # Find the artifact type (look at the most recent switch_artifact_type
    # or propose_design_spec tool_call).
    artifact_type = "poster"
    for step in traj.agent_trace:
        if step.type == "tool_call" and step.tool_name == "switch_artifact_type":
            t = (step.tool_args or {}).get("type")
            if t:
                artifact_type = t

    try:
        critic_system = _load_critic_system_prompt(artifact_type)
    except FileNotFoundError:
        critic_system = ""

    steps = traj.agent_trace
    critic_turn_idx = 0
    for i, step in enumerate(steps):
        if not (step.type == "tool_result" and step.tool_name == "critique"
                and step.tool_result and step.tool_result.status == "ok"):
            continue
        payload = step.tool_result.payload or {}

        # The critic's reasoning step (if any) is appended right after the
        # critique tool_result in our planner loop. Look ahead for it.
        critic_reasoning = ""
        if i + 1 < len(steps):
            maybe = steps[i + 1]
            if maybe.actor == "critic" and maybe.type == "reasoning":
                if maybe.thinking_blocks:
                    critic_reasoning = "\n\n".join(
                        b.thinking for b in maybe.thinking_blocks
                        if not b.is_redacted and b.thinking
                    )

        # The "user" message the critic saw at runtime is artifact-type-
        # specific. For SFT we record a pointer + a text summary rather
        # than re-serializing the whole vision block (which would bloat
        # jsonl with base64 image data).
        user_content = (
            f"[Critic was shown the preview and full DesignSpec for a "
            f"{artifact_type} run (run_id={traj.run_id}, iter={payload.get('iteration', '?')}). "
            f"Preview image: out/runs/{traj.run_id}/final/preview.png]"
        )

        yield {
            "run_id": traj.run_id,
            "turn_idx": critic_turn_idx,
            "actor": "critic",
            "model": traj.metadata.critic_model,
            "provider": provider,
            "messages": [
                {"role": "system", "content": critic_system},
                {"role": "user", "content": user_content},
            ],
            "target": {
                "reasoning_content": critic_reasoning,
                "content": json.dumps(payload, ensure_ascii=False),
                "tool_calls": None,
            },
            "tools": [],
            "metadata": {
                "terminal_status": traj.terminal_status,
                "final_reward": traj.final_reward,
                "turn_stop_reason": "end_turn",
                "turn_usage": {},
                "thinking_budget": traj.metadata.critic_thinking_budget,
                "trajectory_source": traj.metadata.source,
                "critique_iteration": payload.get("iteration"),
                "critique_verdict": payload.get("verdict"),
                "critique_score": payload.get("score"),
            },
        }
        critic_turn_idx += 1


def export_trajectory(
    traj_path: Path,
    *,
    planner_system: str,
    tools: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    try:
        traj = DistillTrajectory.model_validate(json.loads(traj_path.read_text(encoding="utf-8")))
    except Exception as e:
        print(f"  skip {traj_path.name}: {e}", file=sys.stderr)
        return []

    # Skip apply_edits placeholder trajectories (empty agent_trace).
    if not traj.agent_trace or traj.metadata.source == "apply_edits":
        return []

    records: list[dict[str, Any]] = []
    records.extend(_walk_planner_turns(traj, planner_system, tools))
    records.extend(_walk_critic_turns(traj))
    return records


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trajectories", type=Path,
                    default=REPO_ROOT / "out" / "trajectories",
                    help="Directory containing v2 DistillTrajectory JSON files")
    ap.add_argument("--out", type=Path, required=True,
                    help="Output jsonl path")
    ap.add_argument("--min-reward", type=float, default=None,
                    help="Skip trajectories with final_reward < this (None = no filter)")
    ap.add_argument("--source", choices=["agent_run", "apply_edits", "any"], default="agent_run",
                    help="Trajectory source filter (default: agent_run)")
    ap.add_argument("--actor", choices=["planner", "critic", "both"], default="both",
                    help="Which turns to export")
    ap.add_argument("--provider", choices=["anthropic", "openai_compat", "any"], default="any",
                    help="Filter by planner model's inferred provider")
    ap.add_argument("--terminal-status",
                    choices=["pass", "revise", "fail", "max_turns", "abort", "any"],
                    default="any",
                    help="Filter by trajectory terminal_status")
    args = ap.parse_args(argv)

    traj_dir: Path = args.trajectories
    if not traj_dir.exists():
        print(f"error: {traj_dir} does not exist", file=sys.stderr)
        return 2

    out_path: Path = args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)

    planner_system = _load_planner_system_prompt()
    tools = _tool_schemas_openai()

    n_traj_total = 0
    n_traj_kept = 0
    n_records = 0
    stats_provider: collections.Counter[str] = collections.Counter()
    stats_actor: collections.Counter[str] = collections.Counter()
    stats_thinking_chars = 0
    stats_terminal: collections.Counter[str] = collections.Counter()

    with out_path.open("w", encoding="utf-8") as f:
        for traj_path in sorted(traj_dir.glob("*.json")):
            n_traj_total += 1

            try:
                peek = json.loads(traj_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            source = (peek.get("metadata") or {}).get("source", "agent_run")
            if args.source != "any" and source != args.source:
                continue
            reward = peek.get("final_reward")
            if args.min_reward is not None:
                if reward is None or float(reward) < args.min_reward:
                    continue
            if args.terminal_status != "any":
                if peek.get("terminal_status") != args.terminal_status:
                    continue

            records = export_trajectory(traj_path,
                                        planner_system=planner_system, tools=tools)
            if args.actor != "both":
                records = [r for r in records if r["actor"] == args.actor]
            if args.provider != "any":
                records = [r for r in records if r["provider"] == args.provider]
            if not records:
                continue

            n_traj_kept += 1
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
                n_records += 1
                stats_provider[r["provider"]] += 1
                stats_actor[r["actor"]] += 1
                tgt = r.get("target") or {}
                stats_thinking_chars += len(tgt.get("reasoning_content") or "")
                stats_terminal[r["metadata"]["terminal_status"]] += 1

    print(f"Wrote {n_records} records from {n_traj_kept}/{n_traj_total} trajectories to {out_path}")
    print()
    print("Breakdown:")
    print(f"  by actor:          {dict(stats_actor)}")
    print(f"  by provider:       {dict(stats_provider)}")
    print(f"  by terminal_status:{dict(stats_terminal)}")
    print(f"  total thinking chars: {stats_thinking_chars:,}")
    if n_records:
        print(f"  avg thinking chars/record: {stats_thinking_chars // n_records:,}")
    print(f"  jsonl size: {out_path.stat().st_size:,} bytes")

    return 0


if __name__ == "__main__":
    sys.exit(main())
