"""PlannerLoop — handwritten Anthropic tool-use loop.

Drives Claude through propose_design_spec → generate_background → render_text_layer*
→ composite → (critique?) → finalize. Records every assistant turn (text +
tool_use) and every tool_result as paired AgentTraceStep entries with matching
tool_use_id, so the trajectory can be replayed verbatim during SFT.

v1 (training-data capture): Claude extended thinking is enabled by default
(budget controlled via `settings.planner_thinking_budget`). When the
`interleaved-thinking-2025-05-14` beta header is on, thinking blocks may also
appear *between* tool calls, not just at the start of the turn. All thinking +
redacted_thinking content blocks are captured as AgentTraceStep(type='reasoning')
for downstream CoT SFT / RL training data.

INVARIANT: `messages.append({"role": "assistant", "content": resp.content})`
below passes the raw SDK content back verbatim, which is REQUIRED — thinking
blocks carry opaque `signature` fields that Anthropic verifies on the next
turn. Do NOT rebuild the content list by hand; you will break the loop.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from anthropic import Anthropic

from .schema import AgentTraceStep, ThinkingBlockRecord, ToolObservation
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

        # Pre-build thinking config so it's consistent across turns.
        thinking_cfg: dict[str, Any] | None = None
        if self.settings.planner_thinking_budget > 0:
            # Anthropic requires max_tokens > budget_tokens; assert once.
            assert 16384 > self.settings.planner_thinking_budget, (
                f"max_tokens (16384) must exceed planner_thinking_budget "
                f"({self.settings.planner_thinking_budget}); lower the budget or raise max_tokens"
            )
            thinking_cfg = {
                "type": "enabled",
                "budget_tokens": self.settings.planner_thinking_budget,
            }

        extra_headers: dict[str, str] | None = None
        if thinking_cfg is not None and self.settings.enable_interleaved_thinking:
            extra_headers = {"anthropic-beta": "interleaved-thinking-2025-05-14"}

        for turn in range(self.settings.max_planner_turns):
            log("planner.turn", turn=turn + 1, n_messages=len(messages))
            try:
                create_kwargs: dict[str, Any] = {
                    "model": self.settings.planner_model,
                    "max_tokens": 16384,
                    "system": self.system_prompt,
                    "tools": TOOL_SCHEMAS,
                    "messages": messages,
                }
                if thinking_cfg is not None:
                    create_kwargs["thinking"] = thinking_cfg
                if extra_headers is not None:
                    create_kwargs["extra_headers"] = extra_headers
                resp = self.client.messages.create(**create_kwargs)
            except Exception as e:
                self._append(actor="system", type="finalize",
                             text=f"planner API error: {e}")
                raise

            self._total_in += getattr(resp.usage, "input_tokens", 0) or 0
            self._total_out += getattr(resp.usage, "output_tokens", 0) or 0

            thinking_records = _extract_thinking_records(resp.content)
            text_blocks = [b for b in resp.content if getattr(b, "type", None) == "text"]
            tool_uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]

            stop_reason = getattr(resp, "stop_reason", None)
            cache_read = getattr(resp.usage, "cache_read_input_tokens", None)
            cache_create = getattr(resp.usage, "cache_creation_input_tokens", None)

            if thinking_records:
                self._append(
                    actor="planner", type="reasoning",
                    thinking_blocks=thinking_records,
                    model=self.settings.planner_model,
                    stop_reason=stop_reason,
                    cache_read_input_tokens=cache_read,
                    cache_creation_input_tokens=cache_create,
                )
                log("planner.reasoning",
                    n_blocks=len(thinking_records),
                    n_redacted=sum(1 for r in thinking_records if r.is_redacted),
                    turn=turn + 1)

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

                if tu.name == "switch_artifact_type" and obs.status == "ok":
                    self._append(
                        actor="planner", type="artifact_switch",
                        text=f"artifact_type = {ctx.state.get('artifact_type')}",
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
                    # Pick up thinking blocks the critic emitted during its own
                    # Anthropic call (stashed by critique_tool.py). These are
                    # a separate CoT stream from the planner's own reasoning.
                    pending = ctx.state.pop("_pending_critic_thinking", None)
                    if pending:
                        self._append(
                            actor="critic", type="reasoning",
                            thinking_blocks=pending,
                            model=self.settings.critic_model,
                        )

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
            log("tool.call", tool=name, status="unknown")
            return ToolObservation(
                status="error",
                summary=f"unknown tool: {name}",
                next_actions=[f"available tools: {sorted(TOOL_HANDLERS)}"],
            )
        log("tool.call", tool=name)
        try:
            obs = handler(args, ctx=ctx)
            log("tool.result", tool=name, status=obs.status,
                summary=(obs.summary or "")[:240])
            return obs
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


def _extract_thinking_records(content: list[Any]) -> list[ThinkingBlockRecord]:
    """Pull `thinking` and `redacted_thinking` content blocks out of an
    Anthropic `Message.content` list into our on-disk record form.

    Both block types are captured: `redacted_thinking` blocks have no plaintext
    (data is encrypted), but their `data` signature is still recorded so the
    training-data consumer can distinguish and account for them.
    """
    records: list[ThinkingBlockRecord] = []
    for b in content:
        btype = getattr(b, "type", None)
        if btype == "thinking":
            records.append(ThinkingBlockRecord(
                thinking=getattr(b, "thinking", "") or "",
                signature=getattr(b, "signature", "") or "",
                is_redacted=False,
            ))
        elif btype == "redacted_thinking":
            # Redacted blocks expose `data` (encrypted payload) rather than
            # `signature` per the SDK schema; map it to our signature slot to
            # keep the record shape uniform.
            records.append(ThinkingBlockRecord(
                thinking="",
                signature=getattr(b, "data", "") or "",
                is_redacted=True,
            ))
    return records
