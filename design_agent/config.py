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

    repo_root: Path = REPO_ROOT
    fonts_dir: Path = REPO_ROOT / "assets" / "fonts"
    prompts_dir: Path = REPO_ROOT / "prompts"
    out_dir: Path = REPO_ROOT / "out"

    max_critique_iters: int = 2
    max_planner_turns: int = 30
    critic_preview_max_edge: int = 1024

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

    return Settings(
        anthropic_api_key=api_key,
        anthropic_base_url=base_url,
        gemini_api_key=gemini,
        planner_model=planner_model,
        critic_model=critic_model,
    )
