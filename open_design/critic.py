"""Critic — produces a structured CritiqueResult via vision (poster) or
text-only (landing / deck) evaluation.

Poster: vision-based, sends a downscaled preview PNG alongside the DesignSpec
and asks the model to grade against a visual rubric.

Landing (v1.0 #8.5-fix): text-only. The Pillow-rendered preview is a lossy
proxy for the real browser-rendered HTML, so grading it with vision leads to
false fails (tofu emojis, missing CSS, etc.). Instead, we send the DesignSpec
section tree + design_system selection and grade against a content-level
rubric (`prompts/critic-landing.md`).

Deck (v1.0 #7): text-only for the same reason — the per-slide PNG previews
are Pillow approximations of what PowerPoint/Keynote will actually render from
the native TextFrames in the .pptx. The DesignSpec slide tree is the authoritative
structural record, graded against `prompts/critic-deck.md`.

Falls back to a 'fail' verdict if JSON parsing fails so the pipeline still
ends cleanly.
"""

from __future__ import annotations

import base64
import io
import json
import re
from pathlib import Path
from typing import Any

from PIL import Image
from pydantic import ValidationError

from .llm_backend import LLMBackend, make_backend
from .schema import (
    ArtifactType, CritiqueIssue, CritiqueResult, DesignSpec, ThinkingBlockRecord,
)
from .util.logging import log


_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


class Critic:

    def __init__(self, settings):
        self.settings = settings
        self.backend: LLMBackend = make_backend(
            settings, settings.critic_model, role="critic",
        )
        self._poster_prompt: str | None = None
        self._landing_prompt: str | None = None
        self._deck_prompt: str | None = None

    def _system(self, artifact_type: ArtifactType) -> str:
        if artifact_type == ArtifactType.LANDING:
            if self._landing_prompt is None:
                path = self.settings.prompts_dir / "critic-landing.md"
                self._landing_prompt = path.read_text(encoding="utf-8")
            return self._landing_prompt
        if artifact_type == ArtifactType.DECK:
            if self._deck_prompt is None:
                path = self.settings.prompts_dir / "critic-deck.md"
                self._deck_prompt = path.read_text(encoding="utf-8")
            return self._deck_prompt
        if self._poster_prompt is None:
            path = self.settings.prompts_dir / "critic.md"
            self._poster_prompt = path.read_text(encoding="utf-8")
        return self._poster_prompt

    def evaluate(
        self,
        *,
        preview_path: Path,
        design_spec: DesignSpec,
        layer_manifest: list[dict[str, Any]],
        iteration: int,
        max_iters: int,
    ) -> tuple[CritiqueResult, list[ThinkingBlockRecord]]:
        """Returns (CritiqueResult, extended-thinking blocks).

        The thinking list is empty when `critic_thinking_budget == 0` or when
        the model happened to emit no thinking blocks on this turn. The caller
        (critique_tool) stashes non-empty lists for the planner to capture
        into the agent_trace as a separate CoT stream.
        """
        # Landing: text-only. Preview PNG is not representative of the real
        # HTML (emojis → tofu, CSS not applied, etc.), so we skip vision.
        if design_spec.artifact_type == ArtifactType.LANDING:
            return self._evaluate_landing(
                design_spec=design_spec,
                iteration=iteration,
                max_iters=max_iters,
            )

        # Deck: text-only. Per-slide preview.png is a Pillow approximation;
        # the .pptx is the authoritative artifact (live TextFrames in PowerPoint).
        if design_spec.artifact_type == ArtifactType.DECK:
            return self._evaluate_deck(
                design_spec=design_spec,
                iteration=iteration,
                max_iters=max_iters,
            )

        return self._evaluate_with_vision(
            preview_path=preview_path,
            design_spec=design_spec,
            layer_manifest=layer_manifest,
            iteration=iteration,
            max_iters=max_iters,
        )

    def _max_tokens(self) -> int:
        thinking_budget = self.settings.critic_thinking_budget
        return max(2048, thinking_budget + 1024) if thinking_budget > 0 else 2048

    def _evaluate_with_vision(
        self,
        *,
        preview_path: Path,
        design_spec: DesignSpec,
        layer_manifest: list[dict[str, Any]],
        iteration: int,
        max_iters: int,
    ) -> tuple[CritiqueResult, list[ThinkingBlockRecord]]:
        b64, media_type = _downscale_b64(preview_path, self.settings.critic_preview_max_edge)
        user_text = _build_user_text(design_spec, layer_manifest, iteration, max_iters)

        log("critic.request", iter=iteration, max_iters=max_iters,
            preview_kb=len(b64) * 3 // 4 // 1024, mode="vision",
            backend=self.backend.name, model=self.backend.model)

        # Backend builds the right vision content-block shape (Anthropic image
        # block vs OpenAI image_url block) — critic doesn't need to know.
        user_msg = self.backend.vision_user_message(
            image_b64=b64, media_type=media_type, text=user_text,
        )
        resp = self.backend.create_turn(
            system=self._system(design_spec.artifact_type),
            messages=[user_msg],
            tools=[],  # critic doesn't use tools
            thinking_budget=self.settings.critic_thinking_budget,
            max_tokens=self._max_tokens(),
        )

        return _parse_critique(resp.text, iteration, max_iters), resp.thinking_blocks

    def _evaluate_landing(
        self,
        *,
        design_spec: DesignSpec,
        iteration: int,
        max_iters: int,
    ) -> tuple[CritiqueResult, list[ThinkingBlockRecord]]:
        user_text = _build_landing_user_text(design_spec, iteration, max_iters)

        log("critic.request", iter=iteration, max_iters=max_iters,
            mode="text-landing",
            sections=sum(1 for n in (design_spec.layer_graph or [])
                         if getattr(n, "kind", None) == "section"),
            backend=self.backend.name, model=self.backend.model)

        resp = self.backend.create_turn(
            system=self._system(ArtifactType.LANDING),
            messages=[{"role": "user", "content": user_text}],
            tools=[],
            thinking_budget=self.settings.critic_thinking_budget,
            max_tokens=self._max_tokens(),
        )

        return _parse_critique(resp.text, iteration, max_iters), resp.thinking_blocks

    def _evaluate_deck(
        self,
        *,
        design_spec: DesignSpec,
        iteration: int,
        max_iters: int,
    ) -> tuple[CritiqueResult, list[ThinkingBlockRecord]]:
        user_text = _build_deck_user_text(design_spec, iteration, max_iters)

        log("critic.request", iter=iteration, max_iters=max_iters,
            mode="text-deck",
            slides=sum(1 for n in (design_spec.layer_graph or [])
                       if getattr(n, "kind", None) == "slide"),
            backend=self.backend.name, model=self.backend.model)

        resp = self.backend.create_turn(
            system=self._system(ArtifactType.DECK),
            messages=[{"role": "user", "content": user_text}],
            tools=[],
            thinking_budget=self.settings.critic_thinking_budget,
            max_tokens=self._max_tokens(),
        )

        return _parse_critique(resp.text, iteration, max_iters), resp.thinking_blocks


def _downscale_b64(path: Path, max_edge: int) -> tuple[str, str]:
    img = Image.open(path)
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > max_edge:
        if w >= h:
            new = (max_edge, int(h * max_edge / w))
        else:
            new = (int(w * max_edge / h), max_edge)
        img = img.resize(new, Image.LANCZOS)
    if img.mode == "RGBA":
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return base64.b64encode(buf.getvalue()).decode("ascii"), "image/jpeg"


def _build_user_text(spec: DesignSpec, manifest: list[dict[str, Any]],
                     iteration: int, max_iters: int) -> str:
    return (
        f"## Iteration {iteration} of {max_iters}\n\n"
        f"## Brief\n{spec.brief}\n\n"
        f"## DesignSpec snapshot\n```json\n{json.dumps(spec.model_dump(mode='json'), ensure_ascii=False, indent=2)}\n```\n\n"
        f"## Composited layers (manifest)\n```json\n{json.dumps(manifest, ensure_ascii=False, indent=2)}\n```\n\n"
        "Review the attached preview against the rubric in your system prompt. "
        "Output STRICT JSON inside a single ```json ...``` code block matching "
        "the CritiqueResult schema (iteration, verdict, score, issues[], rationale). "
        "Nothing outside the code block."
    )


def _build_landing_user_text(spec: DesignSpec, iteration: int, max_iters: int) -> str:
    """Text-only prompt for landing mode. Flattens the section tree into a
    compact summary so the critic doesn't have to parse nested JSON itself."""
    ds = spec.design_system
    design_system_line = (
        f"style={ds.style}, accent_color={ds.accent_color or '(default)'}"
        if ds else "(no design_system declared — falls back to minimalist)"
    )

    section_blocks: list[str] = []
    sections = list(spec.layer_graph or [])
    for s in sections:
        kind = getattr(s, "kind", None)
        if kind != "section":
            continue
        children = getattr(s, "children", None) or []
        text_lines: list[str] = []
        for child in children:
            if getattr(child, "kind", None) != "text":
                continue
            name = getattr(child, "name", "?")
            size = getattr(child, "font_size_px", "?")
            family = getattr(child, "font_family", "?")
            fill = "?"
            effects = getattr(child, "effects", None)
            if effects is not None:
                fill = getattr(effects, "fill", "?") or "?"
            text = (getattr(child, "text", "") or "").strip()
            text_lines.append(
                f"    - {name}  (font_size={size}, family={family}, fill={fill}): "
                f'"{text}"'
            )
        section_blocks.append(
            f"  ### section `{s.name}` (layer_id={s.layer_id}, "
            f"z_index={s.z_index})\n"
            + ("\n".join(text_lines) if text_lines else "    (no text children)")
        )

    return (
        f"## Iteration {iteration} of {max_iters}\n\n"
        f"## Brief\n{spec.brief}\n\n"
        f"## Design system\n{design_system_line}\n\n"
        f"## Canvas\nwidth={spec.canvas.get('w_px')}, "
        f"height={spec.canvas.get('h_px')} (height is advisory only for landing)\n\n"
        f"## Palette\n{', '.join(spec.palette) if spec.palette else '(empty)'}\n\n"
        f"## Mood tags\n{', '.join(spec.mood) if spec.mood else '(empty)'}\n\n"
        f"## Composition notes\n{spec.composition_notes or '(empty)'}\n\n"
        f"## Section tree ({len(section_blocks)} sections)\n"
        + ("\n".join(section_blocks) if section_blocks else "  (empty)") + "\n\n"
        "## Full DesignSpec JSON (for reference)\n"
        f"```json\n{json.dumps(spec.model_dump(mode='json'), ensure_ascii=False, indent=2)}\n```\n\n"
        "Grade this landing's DesignSpec against the content-level rubric in "
        "your system prompt. You do NOT have a preview image — base your "
        "critique on the section tree + design_system + copy above, and remember "
        "to skip anything that only a visual image could reveal. "
        "Output STRICT JSON inside a single ```json ...``` code block matching "
        "the CritiqueResult schema. Nothing outside the code block."
    )


def _build_deck_user_text(spec: DesignSpec, iteration: int, max_iters: int) -> str:
    """Text-only prompt for deck mode. Flattens the slide tree into a compact
    summary: per slide, its title and any bullets / body text, so the critic
    judges the structural deck without parsing nested JSON."""
    slides = [n for n in (spec.layer_graph or []) if getattr(n, "kind", None) == "slide"]

    slide_blocks: list[str] = []
    for idx, s in enumerate(slides):
        children = getattr(s, "children", None) or []
        lines: list[str] = []
        for child in children:
            kind = getattr(child, "kind", None)
            if kind == "text":
                name = getattr(child, "name", "?")
                size = getattr(child, "font_size_px", "?")
                text = (getattr(child, "text", "") or "").strip()
                if len(text) > 200:
                    text = text[:200] + "…"
                lines.append(f'    - text `{name}` (size={size}): "{text}"')
            elif kind == "image":
                name = getattr(child, "name", "?")
                prompt = (getattr(child, "prompt", "") or "").strip()[:120]
                lines.append(f"    - image `{name}`: {prompt or '(no prompt)'}")
            elif kind == "background":
                name = getattr(child, "name", "?")
                prompt = (getattr(child, "prompt", "") or "").strip()[:120]
                lines.append(f"    - background `{name}`: {prompt or '(no prompt)'}")
        slide_blocks.append(
            f"  ### slide {idx + 1} — `{s.name}` (layer_id={s.layer_id})\n"
            + ("\n".join(lines) if lines else "    (no elements)")
        )

    return (
        f"## Iteration {iteration} of {max_iters}\n\n"
        f"## Brief\n{spec.brief}\n\n"
        f"## Canvas\nwidth={spec.canvas.get('w_px')}, "
        f"height={spec.canvas.get('h_px')} "
        f"(PowerPoint default 1920×1080 = 16:9)\n\n"
        f"## Palette\n{', '.join(spec.palette) if spec.palette else '(empty)'}\n\n"
        f"## Mood tags\n{', '.join(spec.mood) if spec.mood else '(empty)'}\n\n"
        f"## Composition notes\n{spec.composition_notes or '(empty)'}\n\n"
        f"## Slide tree ({len(slide_blocks)} slides)\n"
        + ("\n".join(slide_blocks) if slide_blocks else "  (empty)") + "\n\n"
        "## Full DesignSpec JSON (for reference)\n"
        f"```json\n{json.dumps(spec.model_dump(mode='json'), ensure_ascii=False, indent=2)}\n```\n\n"
        "Grade this deck's DesignSpec against the structural rubric in "
        "your system prompt. You do NOT have a rendered PPTX image — base your "
        "critique on the slide tree + copy above, and remember to skip anything "
        "that only the live PowerPoint/Keynote renderer would reveal. "
        "Output STRICT JSON inside a single ```json ...``` code block matching "
        "the CritiqueResult schema. Nothing outside the code block."
    )


def _parse_critique(text: str, iteration: int, max_iters: int) -> CritiqueResult:
    m = _JSON_BLOCK_RE.search(text)
    payload_str = m.group(1) if m else text.strip()
    try:
        payload = json.loads(payload_str)
    except json.JSONDecodeError:
        log("critic.parse_fail", text_preview=text[:500])
        return CritiqueResult(
            iteration=iteration, verdict="fail", score=0.0,
            issues=[CritiqueIssue(
                severity="blocker", category="artifact",
                description="critic returned non-JSON output",
                suggested_fix="inspect raw text and adjust prompts/critic.md",
            )],
            rationale=text[:1000],
        )
    payload.setdefault("iteration", iteration)
    if iteration >= max_iters and payload.get("verdict") == "revise":
        payload["verdict"] = "fail"
    try:
        return CritiqueResult.model_validate(payload)
    except ValidationError as e:
        log("critic.validate_fail", errors=e.errors(include_url=False))
        return CritiqueResult(
            iteration=iteration, verdict="fail", score=0.0,
            issues=[CritiqueIssue(
                severity="blocker", category="artifact",
                description=f"critic JSON failed schema: {e.errors(include_url=False)[:3]}",
                suggested_fix="tighten the JSON example in prompts/critic.md",
            )],
            rationale=str(payload)[:1000],
        )
