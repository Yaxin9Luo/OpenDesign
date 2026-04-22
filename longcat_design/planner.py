"""PlannerLoop — handwritten tool-use loop, provider-agnostic.

Drives the LLM through propose_design_spec → generate_background →
render_text_layer* → composite → (critique?) → finalize. Records each
turn as paired AgentTraceStep entries (reasoning + tool_call + tool_result)
so the trajectory replays verbatim during SFT.

v2.1 (multi-provider): all LLM access goes through `LLMBackend` so the
same loop works with Claude (Anthropic protocol) OR Kimi / DeepSeek /
Doubao / vLLM-served Qwen (OpenAI-compat protocol). The backend handles
the provider-specific quirks: Anthropic's `thinking` blocks with
signatures vs OpenAI-compat's `reasoning_content` string field; tool_use
content blocks vs tool_calls list; etc. Trajectory shape is identical
regardless of which provider produced it.

Critic-side reasoning still flows through ctx.state["_pending_critic_thinking"]
and gets appended as actor="critic" type="reasoning" steps after each
critique tool_result.
"""

from __future__ import annotations

import json
from typing import Any

from .llm_backend import LLMBackend, ToolCall, TurnResponse, make_backend
from .schema import AgentTraceStep, ThinkingBlockRecord, ToolResultRecord
from .tools import TOOL_HANDLERS, TOOL_SCHEMAS, ToolContext
from .util.logging import log


class PlannerLoop:
    """Owns the conversation with the LLM and the trace it produces.

    Does NOT own ctx — that's runner-level state shared across critic too.
    """

    def __init__(self, settings, system_prompt: str):
        self.settings = settings
        self.system_prompt = system_prompt
        self.backend: LLMBackend = make_backend(
            settings, settings.planner_model, role="planner",
        )
        self.trace: list[AgentTraceStep] = []
        self._step_idx = 0
        self._total_in = 0
        self._total_out = 0
        self._total_cache_read = 0
        self._total_cache_create = 0

    def _next_idx(self) -> int:
        self._step_idx += 1
        return self._step_idx

    def _append(self, **kw: Any) -> None:
        self.trace.append(AgentTraceStep(
            step_idx=self._next_idx(),
            **kw,
        ))

    def run(self, brief: str, ctx: ToolContext) -> list[AgentTraceStep]:
        self._append(actor="user", type="input", text=brief)

        # `messages` lives in the BACKEND'S native format (Anthropic content
        # blocks vs OpenAI message dicts). Only the backend understands its
        # own layout; we hand it back via append_assistant / append_tool_results.
        messages: list[Any] = [{"role": "user", "content": _user_prompt(brief)}]

        thinking_budget = self.settings.planner_thinking_budget
        if thinking_budget > 0:
            assert 16384 > thinking_budget, (
                f"max_tokens (16384) must exceed planner_thinking_budget ({thinking_budget})"
            )

        log("planner.start",
            backend=self.backend.name, model=self.backend.model,
            thinking_budget=thinking_budget,
            interleaved=self.settings.enable_interleaved_thinking)

        for turn in range(self.settings.max_planner_turns):
            log("planner.turn", turn=turn + 1, n_messages=len(messages))
            try:
                resp: TurnResponse = self.backend.create_turn(
                    system=self.system_prompt,
                    messages=messages,
                    tools=TOOL_SCHEMAS,
                    thinking_budget=thinking_budget,
                    max_tokens=16384,
                )
            except Exception as e:
                log("planner.api_error", turn=turn + 1, error=str(e))
                raise

            self._total_in += resp.usage.get("input", 0)
            self._total_out += resp.usage.get("output", 0)
            self._total_cache_read += resp.usage.get("cache_read", 0)
            self._total_cache_create += resp.usage.get("cache_create", 0)

            if resp.thinking_blocks:
                self._append(
                    actor="planner", type="reasoning",
                    thinking_blocks=resp.thinking_blocks,
                    model=self.backend.model,
                    stop_reason=resp.stop_reason,
                    usage=resp.usage,
                )
                log("planner.reasoning",
                    n_blocks=len(resp.thinking_blocks),
                    n_redacted=sum(1 for r in resp.thinking_blocks if r.is_redacted),
                    turn=turn + 1)

            self.backend.append_assistant(messages, resp)

            if not resp.tool_calls:
                if resp.stop_reason == "end_turn":
                    log("planner.end_turn", turn=turn + 1)
                    break
                log("planner.unexpected_stop", turn=turn + 1, stop_reason=resp.stop_reason)
                break

            tool_results_for_api: list[tuple[str, str, bool]] = []
            for tc in resp.tool_calls:
                self._append(
                    actor="planner", type="tool_call",
                    tool_use_id=tc.id, tool_name=tc.name, tool_args=tc.input,
                    model=self.backend.model,
                )
                result = self._invoke(tc.name, tc.input, ctx)
                self._append(
                    actor="tool", type="tool_result",
                    tool_use_id=tc.id, tool_name=tc.name, tool_result=result,
                )

                # Critic's extended-thinking handoff (set by critique_tool.py)
                if tc.name == "critique" and result.status == "ok":
                    pending = ctx.state.pop("_pending_critic_thinking", None)
                    if pending:
                        self._append(
                            actor="critic", type="reasoning",
                            thinking_blocks=pending,
                            model=self.settings.critic_model,
                        )

                tool_results_for_api.append((
                    tc.id,
                    json.dumps(result.model_dump(), ensure_ascii=False),
                    result.status == "error",
                ))

            self.backend.append_tool_results(messages, tool_results_for_api)

            if ctx.state.get("finalized"):
                self._append(actor="planner", type="finalize",
                             text=ctx.state.get("finalize_notes", ""))
                log("planner.finalized", turn=turn + 1)
                break

        return self.trace

    def _invoke(self, name: str, args: dict[str, Any], ctx: ToolContext) -> ToolResultRecord:
        handler = TOOL_HANDLERS.get(name)
        if handler is None:
            log("tool.call", tool=name, status="unknown")
            return ToolResultRecord(
                status="error",
                error_message=f"unknown tool: {name}",
                error_category="validation",
                payload={"available_tools": sorted(TOOL_HANDLERS)},
            )
        log("tool.call", tool=name)
        try:
            result = handler(args, ctx=ctx)
            log("tool.result", tool=name, status=result.status)
            return result
        except Exception as e:
            log("tool.exception", tool=name, error=str(e))
            return ToolResultRecord(
                status="error",
                error_message=f"tool '{name}' raised: {type(e).__name__}: {e}",
                error_category="api",
            )

    @property
    def token_totals(self) -> tuple[int, int]:
        return self._total_in, self._total_out

    @property
    def cache_totals(self) -> tuple[int, int]:
        return self._total_cache_read, self._total_cache_create


def _user_prompt(brief: str) -> str:
    return (
        f"Design brief:\n\n{brief}\n\n"
        "Follow the workflow contract from your system prompt. Begin by calling "
        "`propose_design_spec` with a complete DesignSpec JSON, then proceed."
    )
