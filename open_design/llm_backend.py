"""LLMBackend — provider-agnostic abstraction over Claude / Kimi / Doubao / etc.

Why this exists:
- We want to mix-and-match closed (Anthropic) and open-source (Moonshot Kimi,
  DeepSeek, Qwen, GLM, ...) reasoning models depending on cost / capability /
  data-collection priority.
- Different providers expose reasoning in different shapes: Anthropic uses
  `thinking` content blocks with cryptographic `signature`s for replay;
  OpenAI-compatible providers (Kimi, DeepSeek-R1, Doubao, OpenRouter unified)
  expose `reasoning` / `reasoning_content` as a string field with no signature.
- Tool calling protocol differs too: Anthropic `tool_use` blocks vs OpenAI
  `tool_calls` list of `{id, function: {name, arguments}}`.

Design:
- `LLMBackend` is a small Protocol with three operations: `create_turn`,
  `append_assistant`, `append_tool_results`. The conversation `messages`
  list lives in the backend's NATIVE shape (Anthropic content blocks vs
  OpenAI message objects) — only the backend understands its own layout.
- `TurnResponse` is the unified result the planner / critic see: thinking
  blocks, text, tool calls, stop reason, usage. Mapped from native to our
  schema's `ThinkingBlockRecord` so the trajectory shape is provider-agnostic.

Adding a new provider = subclass `LLMBackend` and register it in `make_backend`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from .schema import ThinkingBlockRecord


@dataclass
class ToolCall:
    """Backend-agnostic tool call (the planner doesn't care which protocol
    produced it)."""
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class TurnResponse:
    """One LLM turn's worth of model output, normalized across providers.

    `raw_assistant_content` is the backend's native message representation,
    held opaquely so `append_assistant` can round-trip it back into the
    conversation in whatever shape the provider expects (critical for
    Anthropic — thinking blocks carry signatures that must survive verbatim).
    """
    thinking_blocks: list[ThinkingBlockRecord] = field(default_factory=list)
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str | None = None  # normalized: end_turn / tool_use / max_tokens / refusal / other
    usage: dict[str, int] = field(default_factory=dict)
    # Native-format payload for round-tripping. Anthropic = list of content
    # blocks (with signatures); OpenAI-compat = a message dict.
    raw_assistant_content: Any = None


# Normalised stop reason vocab — small + provider-agnostic.
StopReason = Literal["end_turn", "tool_use", "max_tokens", "refusal", "other"]


class LLMBackend(Protocol):
    """All planner/critic LLM access goes through this. Implementations live
    below in the same file."""

    name: str         # short id: "anthropic" | "openai_compat"
    model: str        # provider-specific model id

    def create_turn(
        self,
        *,
        system: str,
        messages: list[Any],
        tools: list[dict[str, Any]],
        thinking_budget: int = 0,
        max_tokens: int = 16384,
        extra_headers: dict[str, str] | None = None,
    ) -> TurnResponse:
        ...

    def append_assistant(
        self, messages: list[Any], response: TurnResponse,
    ) -> None:
        """Append assistant turn (with thinking + tool_calls) to the messages
        list in this backend's native format. Mutates in place."""

    def append_tool_results(
        self,
        messages: list[Any],
        results: list[tuple[str, str, bool]],
    ) -> None:
        """Append tool results to the messages list. `results` is a list of
        `(tool_use_id, json_serialized_payload, is_error)` tuples. Mutates."""

    def vision_user_message(
        self, *, image_b64: str, media_type: str, text: str,
    ) -> Any:
        """Build a user message with an inline image + text. Anthropic and
        OpenAI-compat have different content-block shapes for vision; the
        backend handles its own. Returns a single message dict suitable for
        appending to a `messages` list."""


# ─────────────────────────── Anthropic ────────────────────────────────────


class AnthropicBackend:
    """Native Anthropic Messages API. Used for Claude models.

    Reasoning: `thinking` + `redacted_thinking` content blocks with
    cryptographic `signature` / `data` fields. Both must round-trip
    verbatim or the next turn 400s.

    Tool calling: native `tool_use` content blocks; results return as
    `tool_result` blocks inside a user-role message.
    """

    name = "anthropic"

    def __init__(self, settings, model: str):
        from anthropic import Anthropic

        self.model = model
        self._enable_interleaved = settings.enable_interleaved_thinking
        client_kwargs: dict[str, Any] = {"api_key": settings.anthropic_api_key}
        if settings.anthropic_base_url:
            client_kwargs["base_url"] = settings.anthropic_base_url
        self.client = Anthropic(**client_kwargs)

    def create_turn(
        self,
        *,
        system: str,
        messages: list[Any],
        tools: list[dict[str, Any]],
        thinking_budget: int = 0,
        max_tokens: int = 16384,
        extra_headers: dict[str, str] | None = None,
    ) -> TurnResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "tools": tools,
            "messages": messages,
        }
        if thinking_budget > 0:
            kwargs["thinking"] = {
                "type": "enabled", "budget_tokens": thinking_budget,
            }
        merged_headers: dict[str, str] = {}
        if thinking_budget > 0 and self._enable_interleaved:
            merged_headers["anthropic-beta"] = "interleaved-thinking-2025-05-14"
        if extra_headers:
            merged_headers.update(extra_headers)
        if merged_headers:
            kwargs["extra_headers"] = merged_headers

        resp = self.client.messages.create(**kwargs)

        thinking: list[ThinkingBlockRecord] = []
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for b in resp.content:
            t = getattr(b, "type", None)
            if t == "thinking":
                thinking.append(ThinkingBlockRecord(
                    thinking=getattr(b, "thinking", "") or "",
                    signature=getattr(b, "signature", "") or "",
                    is_redacted=False,
                ))
            elif t == "redacted_thinking":
                thinking.append(ThinkingBlockRecord(
                    thinking="",
                    signature=getattr(b, "data", "") or "",
                    is_redacted=True,
                ))
            elif t == "text":
                text_parts.append(b.text)
            elif t == "tool_use":
                tool_calls.append(ToolCall(
                    id=b.id, name=b.name, input=dict(b.input),
                ))

        return TurnResponse(
            thinking_blocks=thinking,
            text="".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=_normalize_stop(getattr(resp, "stop_reason", None), "anthropic"),
            usage={
                "input": getattr(resp.usage, "input_tokens", 0) or 0,
                "output": getattr(resp.usage, "output_tokens", 0) or 0,
                "cache_read": getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
                "cache_create": getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
            },
            raw_assistant_content=resp.content,
        )

    def append_assistant(self, messages: list[Any], response: TurnResponse) -> None:
        # Pass raw SDK content list back verbatim — signatures must survive.
        messages.append({"role": "assistant", "content": response.raw_assistant_content})

    def append_tool_results(
        self,
        messages: list[Any],
        results: list[tuple[str, str, bool]],
    ) -> None:
        blocks = [
            {
                "type": "tool_result",
                "tool_use_id": tu_id,
                "content": payload,
                "is_error": is_err,
            }
            for tu_id, payload, is_err in results
        ]
        messages.append({"role": "user", "content": blocks})

    def vision_user_message(
        self, *, image_b64: str, media_type: str, text: str,
    ) -> dict[str, Any]:
        return {
            "role": "user",
            "content": [
                {"type": "image",
                 "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
                {"type": "text", "text": text},
            ],
        }


# ──────────────────────── OpenAI-compatible ───────────────────────────────


class OpenAICompatBackend:
    """OpenAI Chat Completions-compatible backend.

    Used for: OpenRouter (any model), Moonshot Kimi, DeepSeek, ByteDance
    Doubao, vLLM/SGLang self-host, OpenAI itself.

    Reasoning extraction is best-effort across provider field names:
    `reasoning` (OpenRouter unified), `reasoning_content` (DeepSeek/Kimi
    native), `thought` (some local serves). No `signature` mechanism —
    on round-trip we preserve `reasoning_content` in the message dict but
    providers don't verify it; if rejected we drop it (caller can fall
    back via `drop_reasoning_on_replay=True`).
    """

    name = "openai_compat"

    def __init__(
        self,
        settings,
        model: str,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
    ):
        from openai import OpenAI

        self.model = model
        # Resolve base_url + api_key with a precedence chain so a single
        # OPENROUTER_API_KEY can power both Anthropic-style and OpenAI-style
        # routing without extra config.
        resolved_url = (
            base_url
            or getattr(settings, "openai_compat_base_url", None)
            or "https://openrouter.ai/api/v1"
        )
        resolved_key = (
            api_key
            or getattr(settings, "openai_compat_api_key", None)
            or settings.anthropic_api_key  # OPENROUTER_API_KEY lives here when OR-mode
        )
        self.client = OpenAI(base_url=resolved_url, api_key=resolved_key)

    def create_turn(
        self,
        *,
        system: str,
        messages: list[Any],
        tools: list[dict[str, Any]],
        thinking_budget: int = 0,
        max_tokens: int = 16384,
        extra_headers: dict[str, str] | None = None,
    ) -> TurnResponse:
        # Convert our Anthropic-shape tool schemas to OpenAI function shape.
        oai_tools = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t["input_schema"],
                },
            }
            for t in tools
        ]
        # Prepend system as first message (OpenAI style).
        oai_messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        oai_messages.extend(messages)

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": oai_messages,
            "tools": oai_tools,
            "max_tokens": max_tokens,
        }
        if thinking_budget > 0:
            # OpenRouter unified reasoning param — providers that don't support
            # it ignore it silently. For native Moonshot / DeepSeek API the
            # field name may differ but reasoning is on by default for thinking
            # variants of those models.
            kwargs["extra_body"] = {
                "reasoning": {"max_tokens": thinking_budget},
            }
        if extra_headers:
            kwargs["extra_headers"] = extra_headers

        resp = self.client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        msg = choice.message

        # Reasoning extraction — try common field names + raw dict fallback.
        reasoning_text = ""
        for attr in ("reasoning", "reasoning_content", "thought"):
            v = getattr(msg, attr, None)
            if v:
                reasoning_text = v
                break
        if not reasoning_text:
            try:
                msg_dict = msg.model_dump()
                for k in ("reasoning", "reasoning_content", "thought"):
                    if msg_dict.get(k):
                        reasoning_text = msg_dict[k]
                        break
            except Exception:
                pass

        thinking: list[ThinkingBlockRecord] = []
        if reasoning_text:
            thinking.append(ThinkingBlockRecord(
                thinking=reasoning_text,
                signature="",  # OpenAI-compat has no signature mechanism
                is_redacted=False,
            ))

        # Tool calls
        tool_calls: list[ToolCall] = []
        for tc in (msg.tool_calls or []):
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                # Some providers occasionally emit non-JSON tool args; preserve
                # the raw string so the policy can learn the failure mode.
                args = {"_raw_tool_args": tc.function.arguments or ""}
            tool_calls.append(ToolCall(
                id=tc.id, name=tc.function.name, input=args,
            ))

        # Cache token telemetry (DeepSeek + others expose under prompt_tokens_details)
        cache_read = 0
        if resp.usage is not None:
            ptd = getattr(resp.usage, "prompt_tokens_details", None)
            if ptd is not None:
                cache_read = getattr(ptd, "cached_tokens", 0) or 0

        # Build the assistant message dict for round-tripping into the next turn.
        # Some providers (e.g. Anthropic-via-OpenRouter via OpenAI endpoint)
        # reject `reasoning_content` on subsequent turns, so we DON'T include
        # it in the round-tripped message. The original reasoning is preserved
        # in our trajectory via thinking_blocks.
        assistant_msg: dict[str, Any] = {"role": "assistant"}
        if msg.content is not None:
            assistant_msg["content"] = msg.content
        if msg.tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ]

        return TurnResponse(
            thinking_blocks=thinking,
            text=msg.content or "",
            tool_calls=tool_calls,
            stop_reason=_normalize_stop(choice.finish_reason, "openai"),
            usage={
                "input": getattr(resp.usage, "prompt_tokens", 0) or 0,
                "output": getattr(resp.usage, "completion_tokens", 0) or 0,
                "cache_read": cache_read,
                "cache_create": 0,
            },
            raw_assistant_content=assistant_msg,
        )

    def append_assistant(self, messages: list[Any], response: TurnResponse) -> None:
        messages.append(response.raw_assistant_content)

    def append_tool_results(
        self,
        messages: list[Any],
        results: list[tuple[str, str, bool]],
    ) -> None:
        for tu_id, payload, _is_err in results:
            messages.append({
                "role": "tool",
                "tool_call_id": tu_id,
                "content": payload,
            })

    def vision_user_message(
        self, *, image_b64: str, media_type: str, text: str,
    ) -> dict[str, Any]:
        # OpenAI-compat uses an `image_url` block with a data: URI string.
        return {
            "role": "user",
            "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:{media_type};base64,{image_b64}"}},
                {"type": "text", "text": text},
            ],
        }


# ──────────────────────── helpers + factory ───────────────────────────────


def _normalize_stop(raw: str | None, provider: str) -> str:
    """Map provider-specific finish/stop reason strings to a small shared vocab."""
    if raw is None:
        return "other"
    if provider == "anthropic":
        # Anthropic returns: end_turn, tool_use, max_tokens, stop_sequence, refusal, pause_turn
        if raw in ("end_turn", "tool_use", "max_tokens", "refusal"):
            return raw
        return "other"
    if provider == "openai":
        # OpenAI: stop, tool_calls, length, content_filter, function_call
        m = {"stop": "end_turn", "tool_calls": "tool_use",
             "length": "max_tokens", "content_filter": "refusal",
             "function_call": "tool_use"}
        return m.get(raw, "other")
    return "other"


def _auto_provider(model: str) -> str:
    """Pick a backend based on model id when settings don't pin one."""
    m = model.lower()
    if m.startswith("anthropic/") or m.startswith("claude-"):
        return "anthropic"
    # Anything else routed through OpenRouter / native OpenAI-compat APIs.
    # Covers: moonshotai/kimi-*, deepseek/*, qwen/*, mistralai/*, meta-llama/*,
    # google/* (Gemini-OpenAI-compat), openai/*, etc.
    return "openai_compat"


def make_backend(settings, model: str, *, role: str = "planner") -> LLMBackend:
    """Construct a backend instance for `model`.

    Provider precedence: settings.<role>_provider override > auto-detect from
    model id prefix. The settings hook is `planner_provider` / `critic_provider`
    (Literal["auto", "anthropic", "openai_compat"], default "auto").
    """
    explicit = getattr(settings, f"{role}_provider", "auto")
    provider = explicit if explicit != "auto" else _auto_provider(model)

    if provider == "anthropic":
        return AnthropicBackend(settings, model)
    if provider == "openai_compat":
        return OpenAICompatBackend(settings, model)
    raise ValueError(f"unknown LLM provider: {provider!r}")
