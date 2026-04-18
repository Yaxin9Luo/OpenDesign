"""Critic — vision-based self-review producing a structured CritiqueResult.

Downscales preview before sending to respect vision input caps. Asks the model
for strict JSON; if parsing fails, falls back to a 'fail' verdict so the
pipeline still ends cleanly.
"""

from __future__ import annotations

import base64
import io
import json
import re
from pathlib import Path
from typing import Any

from anthropic import Anthropic
from PIL import Image
from pydantic import ValidationError

from .schema import CritiqueIssue, CritiqueResult, DesignSpec
from .util.logging import log


_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


class Critic:

    def __init__(self, settings):
        self.settings = settings
        client_kwargs: dict[str, Any] = {"api_key": settings.anthropic_api_key}
        if settings.anthropic_base_url:
            client_kwargs["base_url"] = settings.anthropic_base_url
        self.client = Anthropic(**client_kwargs)
        self._system_prompt: str | None = None

    def _system(self) -> str:
        if self._system_prompt is None:
            path = self.settings.prompts_dir / "critic.md"
            self._system_prompt = path.read_text(encoding="utf-8")
        return self._system_prompt

    def evaluate(
        self,
        *,
        preview_path: Path,
        design_spec: DesignSpec,
        layer_manifest: list[dict[str, Any]],
        iteration: int,
        max_iters: int,
    ) -> CritiqueResult:
        b64, media_type = _downscale_b64(preview_path, self.settings.critic_preview_max_edge)

        user_text = _build_user_text(design_spec, layer_manifest, iteration, max_iters)

        log("critic.request", iter=iteration, max_iters=max_iters,
            preview_kb=len(b64) * 3 // 4 // 1024)

        resp = self.client.messages.create(
            model=self.settings.critic_model,
            max_tokens=2048,
            system=self._system(),
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image",
                     "source": {"type": "base64", "media_type": media_type, "data": b64}},
                    {"type": "text", "text": user_text},
                ],
            }],
        )

        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        result = _parse_critique(text, iteration, max_iters)
        return result


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
