"""Runtime settings — env vars, model ids, paths, caps.

Two LLM backends supported (auto-detected from env):
- OpenRouter: set OPENROUTER_API_KEY → routed via /api/v1/messages with
  model `anthropic/claude-opus-4.7`. Takes precedence.
- Anthropic stock: set ANTHROPIC_API_KEY → standard endpoint with
  model `claude-opus-4-7`.

Either way, the Anthropic Python SDK is used (OpenRouter exposes an
Anthropic-compatible /messages endpoint, so the same client + tool-use
protocol works with just a base_url swap).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env", override=True)  # .env wins over shell-exported empties


# Anthropic SDK appends "/v1/messages" itself, so the base URL must NOT
# include the /v1 prefix — otherwise the request hits /api/v1/v1/messages → 404.
OPENROUTER_BASE_URL = "https://openrouter.ai/api"
OPENROUTER_DEFAULT_MODEL = "anthropic/claude-opus-4.7"
ANTHROPIC_DEFAULT_MODEL = "claude-opus-4-7"


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str
    anthropic_base_url: str | None              # None → stock Anthropic endpoint
    gemini_api_key: str

    planner_model: str
    critic_model: str
    image_model: str = "gemini-3-pro-image-preview"
    # v1.1 paper2any: Sonnet-class model used by ingest_document for
    # PDF structure extraction + figure bbox location. Much faster than
    # Opus for large PDFs (~3-5× shorter wall time) and plenty capable
    # for "extract title / sections / figures" — not a reasoning task.
    # Override via INGEST_MODEL env var.
    ingest_model: str = "anthropic/claude-sonnet-4-6"
    # Explicit HTTP timeout (seconds) for ingest Anthropic calls so a
    # stalled request fails fast instead of hanging 20+ minutes.
    ingest_http_timeout: float = 600.0  # 10 minutes

    repo_root: Path = REPO_ROOT
    fonts_dir: Path = REPO_ROOT / "assets" / "fonts"
    prompts_dir: Path = REPO_ROOT / "prompts"
    out_dir: Path = REPO_ROOT / "out"

    max_critique_iters: int = 2
    max_planner_turns: int = 30
    critic_preview_max_edge: int = 1024

    # v1 (training-data capture) — Claude extended thinking
    #   budget=0 → thinking disabled (dev / cheap runs).
    #   interleaved flag sends the `interleaved-thinking-2025-05-14` beta header
    #   so Claude may emit thinking blocks *between* tool calls, not only at the
    #   start of each turn. Required for high-quality tool-use CoT lanes.
    planner_thinking_budget: int = 10000
    critic_thinking_budget: int = 10000
    enable_interleaved_thinking: bool = True

    fonts: dict[str, str] = field(default_factory=lambda: {
        "NotoSansSC-Bold": "NotoSansSC-Bold.otf",
        "NotoSerifSC-Bold": "NotoSerifSC-Bold.otf",
    })
    default_text_font: str = "NotoSansSC-Bold"
    default_title_font: str = "NotoSerifSC-Bold"

    @property
    def llm_backend(self) -> str:
        return "openrouter" if self.anthropic_base_url else "anthropic"


def load_settings() -> Settings:
    gemini = os.getenv("GEMINI_API_KEY", "").strip()
    if not gemini:
        raise RuntimeError("GEMINI_API_KEY missing — copy .env.example to .env and fill it in")

    or_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    ant_key = os.getenv("ANTHROPIC_API_KEY", "").strip()

    base_url_override = os.getenv("ANTHROPIC_BASE_URL", "").strip() or None

    if or_key:
        # OpenRouter mode: always pin to OpenRouter's URL, ignore any stray
        # ANTHROPIC_BASE_URL from the shell that might point at stock Anthropic.
        api_key = or_key
        base_url = OPENROUTER_BASE_URL
        default_model = OPENROUTER_DEFAULT_MODEL
    elif ant_key:
        api_key = ant_key
        base_url = base_url_override
        default_model = ANTHROPIC_DEFAULT_MODEL
    else:
        raise RuntimeError(
            "No LLM credential — set OPENROUTER_API_KEY (preferred) or "
            "ANTHROPIC_API_KEY in .env"
        )

    planner_model = os.getenv("PLANNER_MODEL", "").strip() or default_model
    critic_model = os.getenv("CRITIC_MODEL", "").strip() or default_model
    # v1.1: default ingest to Sonnet (fast + cheap for "read this PDF").
    # Respects OpenRouter vs stock naming convention via default_model prefix.
    if or_key:
        ingest_default = "anthropic/claude-sonnet-4-6"
    else:
        ingest_default = "claude-sonnet-4-6"
    ingest_model = os.getenv("INGEST_MODEL", "").strip() or ingest_default

    planner_budget = _parse_int_env("PLANNER_THINKING_BUDGET", 10000)
    critic_budget = _parse_int_env("CRITIC_THINKING_BUDGET", 10000)
    interleaved = os.getenv("ENABLE_INTERLEAVED_THINKING", "1").strip() not in (
        "0", "false", "False", "no", "",
    )
    ingest_timeout = float(_parse_int_env("INGEST_HTTP_TIMEOUT", 600))

    return Settings(
        anthropic_api_key=api_key,
        anthropic_base_url=base_url,
        gemini_api_key=gemini,
        planner_model=planner_model,
        critic_model=critic_model,
        ingest_model=ingest_model,
        ingest_http_timeout=ingest_timeout,
        planner_thinking_budget=planner_budget,
        critic_thinking_budget=critic_budget,
        enable_interleaved_thinking=interleaved,
    )


def _parse_int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default
