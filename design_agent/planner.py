"""PlannerLoop — handwritten Anthropic tool-use loop.

Drives Claude through propose_design_spec → generate_background → render_text_layer*
→ composite → (critique?) → finalize. Records every assistant turn (text +
tool_use) and every tool_result as paired AgentTraceStep entries with matching
tool_use_id, so the trajectory can be replayed verbatim during SFT.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from anthropic import Anthropic

from .schema import AgentTraceStep, ToolObservation
from .tools import TOOL_HANDLERS, TOOL_SCHEMAS, ToolContext
from .util.logging import log


class PlannerLoop:
    """Owns the conversation with Claude and the trace it produces.

    Does NOT own ctx — that's runner-level state shared across critic too.
    """

    def __init__(self, settings, system_prompt: str):
        self.settings = settings
        self.system_prompt = system_prompt
        client_kwargs: dict[str, Any] = {"api_key": settings.anthropic_api_key}
        if settings.anthropic_base_url:
            client_kwargs["base_url"] = settings.anthropic_base_url
        self.client = Anthropic(**client_kwargs)
        self.trace: list[AgentTraceStep] = []
        self._step_idx = 0
        self._total_in = 0
        self._total_out = 0

    def _next_idx(self) -> int:
        self._step_idx += 1
        return self._step_idx

    def _append(self, **kw: Any) -> None:
        self.trace.append(AgentTraceStep(
            step_idx=self._next_idx(),
            timestamp=datetime.now(),
            **kw,
        ))

    def run(self, brief: str, ctx: ToolContext) -> list[AgentTraceStep]:
        self._append(actor="user", type="input", text=brief)

        messages: list[dict[str, Any]] = [
            {"role": "user", "content": _user_prompt(brief)},
        ]

        for turn in range(self.settings.max_planner_turns):
            log("planner.turn", turn=turn + 1, n_messages=len(messages))
            try:
                resp = self.client.messages.create(
                    model=self.settings.planner_model,
                    max_tokens=4096,
                    system=self.system_prompt,
                    tools=TOOL_SCHEMAS,
                    messages=messages,
                )
            except Exception as e:
                self._append(actor="system", type="finalize",
                             text=f"planner API error: {e}")
                raise

            self._total_in += getattr(resp.usage, "input_tokens", 0) or 0
            self._total_out += getattr(resp.usage, "output_tokens", 0) or 0

            text_blocks = [b for b in resp.content if getattr(b, "type", None) == "text"]
            tool_uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]

            for tb in text_blocks:
                self._append(actor="planner", type="thought",
                             text=tb.text, model=self.settings.planner_model,
                             input_tokens=resp.usage.input_tokens if tool_uses == [] else None,
                             output_tokens=resp.usage.output_tokens if tool_uses == [] else None)

            messages.append({"role": "assistant", "content": resp.content})

            if not tool_uses:
                if resp.stop_reason == "end_turn":
                    log("planner.end_turn", turn=turn + 1)
                    break
                self._append(actor="planner", type="thought",
                             text=f"[unexpected stop_reason={resp.stop_reason}]")
                break

            tool_results_for_api: list[dict[str, Any]] = []
            for tu in tool_uses:
                self._append(
                    actor="planner", type="tool_call",
                    tool_use_id=tu.id, tool_name=tu.name, tool_args=dict(tu.input),
                    model=self.settings.planner_model,
                )
                obs = self._invoke(tu.name, dict(tu.input), ctx)
                self._append(
                    actor="tool", type="tool_result",
                    tool_use_id=tu.id, tool_name=tu.name, observation=obs,
                )

                if tu.name == "propose_design_spec" and obs.status == "ok":
                    spec = ctx.state.get("design_spec")
                    if spec is not None:
                        self._append(actor="planner", type="design_spec",
                                     spec_snapshot=spec)

                if tu.name == "critique" and obs.status == "ok":
                    crits = ctx.state.get("critique_results", [])
                    if crits:
                        latest = crits[-1]
                        self._append(actor="critic", type="critique",
                                     text=latest.rationale,
                                     observation=ToolObservation(
                                         status="ok",
                                         summary=f"verdict={latest.verdict} score={latest.score:.2f}",
                                         artifacts=obs.artifacts,
                                     ))

                tool_results_for_api.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": json.dumps(obs.model_dump(), ensure_ascii=False),
                    "is_error": obs.status == "error",
                })

            messages.append({"role": "user", "content": tool_results_for_api})

            if ctx.state.get("finalized"):
                log("planner.finalized", turn=turn + 1)
                break

        return self.trace

    def _invoke(self, name: str, args: dict[str, Any], ctx: ToolContext) -> ToolObservation:
        handler = TOOL_HANDLERS.get(name)
        if handler is None:
            return ToolObservation(
                status="error",
                summary=f"unknown tool: {name}",
                next_actions=[f"available tools: {sorted(TOOL_HANDLERS)}"],
            )
        try:
            return handler(args, ctx=ctx)
        except Exception as e:
            log("tool.exception", tool=name, error=str(e))
            return ToolObservation(
                status="error",
                summary=f"tool '{name}' raised: {type(e).__name__}: {e}",
            )

    @property
    def token_totals(self) -> tuple[int, int]:
        return self._total_in, self._total_out


def _user_prompt(brief: str) -> str:
    return (
        f"Design brief:\n\n{brief}\n\n"
        "Follow the workflow contract from your system prompt. Begin by calling "
        "`propose_design_spec` with a complete DesignSpec JSON, then proceed."
    )
