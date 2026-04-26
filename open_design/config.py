"""Runtime settings — env vars, model ids, paths, caps.

Multi-provider LLM backend (v2.1):
- Planner / Critic LLM access goes through `llm_backend.LLMBackend` so we
  can mix Anthropic Claude with OpenAI-compatible models (Moonshot Kimi,
  DeepSeek, Doubao, vLLM-served Qwen, etc.) without changing tool schemas
  or trajectory shape.
- Provider is auto-detected from model id prefix: `anthropic/...` and
  `claude-...` → Anthropic backend; everything else → OpenAI-compat
  backend (defaults to OpenRouter base_url).
- Override per-role: `PLANNER_PROVIDER=anthropic|openai_compat|auto`
  (same for `CRITIC_PROVIDER`).
- Default planner is `deepseek/deepseek-v3.2-exp` (164k context + sparse
  attention designed for long inputs — paper2any-friendly). Critic is
  `qwen/qwen-vl-max` (multimodal — can grade rendered output, not just
  the structural tree). Both swappable via env: `PLANNER_MODEL=...`,
  `CRITIC_MODEL=anthropic/claude-opus-4.7`, etc.

Credentials (any subset works depending on which providers you call):
- `OPENROUTER_API_KEY`: powers BOTH Anthropic-via-OpenRouter and the
  OpenAI-compat backend (single key, both endpoints).
- `ANTHROPIC_API_KEY`: stock Anthropic endpoint.
- `OPENAI_COMPAT_API_KEY` + `OPENAI_COMPAT_BASE_URL`: explicit override
  for self-hosted vLLM / native Moonshot / DeepSeek / Doubao endpoints.
- `GEMINI_API_KEY`: only required when `IMAGE_PROVIDER` resolves to
  `gemini` (auto-routing on a model id starting with `gemini-` or
  `imagen-`). The v2.5 default is seedream via OpenRouter, so most
  users don't need this.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env", override=True)  # .env wins over shell-exported empties


# Anthropic SDK appends "/v1/messages" itself, so the base URL must NOT
# include the /v1 prefix — otherwise the request hits /api/v1/v1/messages → 404.
OPENROUTER_BASE_URL_ANTHROPIC = "https://openrouter.ai/api"
# OpenAI client DOES want the /v1 prefix (it appends /chat/completions itself).
OPENROUTER_BASE_URL_OPENAI = "https://openrouter.ai/api/v1"

# Multi-provider model defaults — user can override via env vars.
#
# v2.5.1 (2026-04-25): planner switched to DeepSeek V3.2-exp (164k ctx,
# better paper2any convergence than Kimi K2.6 — Kimi stalled out on
# paper-deck max_turns; V3.2 produced a real 12-slide deck). Critic
# switched to Qwen-VL-Max for multimodal vision.
#
# v2.7 (2026-04-25): planner reverted to Kimi K2.6 after the v2.7
# provenance dogfood — V3.2-exp emitted body bullets with raw numbers
# AND, when forced to cite by the v2.7 evidence_quote rule, FABRICATED
# the quotes (iter 2 produced 7 quote_not_in_source failures). The
# validator caught everything, but V3.2 demonstrably can't follow the
# "MUST be verbatim substring of ingest" hard constraint. Kimi K2.6 is
# an agent-coding model with stronger structured-output discipline, and
# the v2.5.1 max_turns failure was on the v2.5 prompt density — after
# v2.6.1 enhancer compression + v2.7 schema clarity, the prompt is
# leaner and Kimi should reach a complete deck spec.
DEFAULT_PLANNER_MODEL = "moonshotai/kimi-k2.6"            # agent-coding model
DEFAULT_CRITIC_MODEL = "qwen/qwen-vl-max"                 # multimodal — sees what got rendered
ANTHROPIC_FALLBACK_PLANNER = "claude-opus-4-7"            # if user only has ANTHROPIC_API_KEY
ANTHROPIC_FALLBACK_CRITIC = "claude-opus-4-7"

# v2.4 Prompt Enhancer — runs once before planner.start, converting a raw
# user brief into a structured multi-section enhanced brief. Defaults to
# Kimi K2.6 (same as planner+critic) to keep the dev-loop cheap; users
# override via `ENHANCER_MODEL=anthropic/claude-opus-4-7` (or any other
# id) when they want stronger brief authoring. The fallback below is
# used only when the user has ANTHROPIC_API_KEY but no OPENROUTER_API_KEY
# — Kimi is unreachable on the stock Anthropic endpoint, so we drop back
# to Claude transparently rather than fail-loud at startup.
DEFAULT_ENHANCER_MODEL = "moonshotai/kimi-k2.6"
ANTHROPIC_FALLBACK_ENHANCER = "claude-opus-4-7"

# v2.8.0 ClaimGraph extractor — runs between enhancer and planner when
# the input attaches a PDF. Default Kimi K2.6 (same agent-coding model as
# planner+enhancer; strict JSON output discipline). Users override via
# `CLAIM_GRAPH_MODEL=anthropic/claude-opus-4-7` when capability matters
# more than cost. Anthropic-only fallback strips the OpenRouter prefix
# the same way enhancer fallback does.
DEFAULT_CLAIM_GRAPH_MODEL = "moonshotai/kimi-k2.6"
ANTHROPIC_FALLBACK_CLAIM_GRAPH = "claude-opus-4-7"


ProviderChoice = Literal["auto", "anthropic", "openai_compat"]
ImageProviderChoice = Literal["auto", "gemini", "openrouter"]
SectionNumberPolicy = Literal["renumber", "strip", "preserve"]


@dataclass(frozen=True)
class Settings:
    # Anthropic credentials (also reused as OpenRouter creds when in OR mode)
    anthropic_api_key: str
    anthropic_base_url: str | None              # None → stock Anthropic endpoint

    # NBP (Gemini) credential — only required when image_provider resolves
    # to gemini. Empty string is fine when the user runs seedream / any
    # other OpenRouter image model. Validated lazily inside
    # `GeminiImageBackend.__init__` so an unset key on the seedream path
    # doesn't crash startup.
    gemini_api_key: str

    # Per-role model + provider selection
    planner_model: str
    critic_model: str
    planner_provider: ProviderChoice = "auto"
    critic_provider: ProviderChoice = "auto"

    # v2.4 Prompt Enhancer stage — runs before planner.start. Defaults to
    # Kimi K2.6 (cheap dev-loop, same provider as planner+critic); users
    # pin a stronger model via ENHANCER_MODEL=anthropic/claude-opus-4-7
    # when brief authoring matters more than per-run cost.
    # `enable_prompt_enhancer` gates the whole stage; the `--skip-enhancer`
    # CLI flag sets it to False per-run.
    enhancer_model: str = DEFAULT_ENHANCER_MODEL
    enhancer_provider: ProviderChoice = "auto"
    enhancer_thinking_budget: int = 10000
    enable_prompt_enhancer: bool = True

    # v2.8.0 ClaimGraph extractor stage — runs between the enhancer and
    # the planner whenever the brief attaches a PDF. `claim_graph_max_turns`
    # caps the sub-agent's loop in case the model never calls
    # `report_claim_graph`; on hit we synthesize a sentinel graph and the
    # runner drops it back to None so the planner degrades to v2.7.3
    # chapter-order behavior. `enable_claim_graph` gates the whole stage;
    # the `--no-claim-graph` CLI flag sets it to False per-run.
    claim_graph_model: str = DEFAULT_CLAIM_GRAPH_MODEL
    claim_graph_provider: ProviderChoice = "auto"
    claim_graph_max_turns: int = 15
    claim_graph_thinking_budget: int = 8000
    enable_claim_graph: bool = True

    # OpenAI-compat backend connection (used when provider resolves to openai_compat)
    openai_compat_api_key: str | None = None    # falls back to anthropic_api_key when OR
    openai_compat_base_url: str = OPENROUTER_BASE_URL_OPENAI

    # OpenRouter key kept separate for the v1.2 ingest VLM path (util/vlm.py)
    openrouter_api_key: str | None = None

    # v2.5 multi-provider image generation. `image_model` follows the same
    # auto-detect rule as planner/critic: `gemini-*` / `imagen-*` route to
    # the GeminiImageBackend, everything else to OpenRouter (chat/completions
    # with modalities=["image","text"]). Default is seedream 4.5 via
    # OpenRouter to keep the dev loop cheap; users override with
    # `IMAGE_MODEL=gemini-3-pro-image-preview` (etc) or pin a provider via
    # `IMAGE_PROVIDER=auto|gemini|openrouter`.
    image_model: str = "bytedance-seed/seedream-4.5"
    image_provider: ImageProviderChoice = "auto"

    # v1.2 paper2any: VLM used by ingest_document
    ingest_model: str = "qwen/qwen-vl-max"
    ingest_http_timeout: float = 600.0

    repo_root: Path = REPO_ROOT
    fonts_dir: Path = REPO_ROOT / "assets" / "fonts"
    prompts_dir: Path = REPO_ROOT / "prompts"
    out_dir: Path = REPO_ROOT / "out"

    max_critique_iters: int = 2
    max_planner_turns: int = 30
    critic_preview_max_edge: int = 1024

    # v2.7.2 deck section-number policy — applied inside `_composite_deck`
    # before write_pptx, so renderer + apply-edits both see consistent
    # numbering. "renumber" (default) walks slides in order, assigns §1,
    # §1.1, §2, ... using a sub-rhythm heuristic; "strip" clears every
    # SlideNode.section_number; "preserve" passes the planner's values
    # through unchanged. Override per-run via `SECTION_NUMBER_POLICY=...`.
    section_number_policy: SectionNumberPolicy = "renumber"

    # v2.7.3 — Vision critic sub-agent (CriticAgent in
    # open_design/agents/critic_agent.py). The critic now runs as an
    # independent loop with its own LLMBackend instance, its own turn
    # budget, and its own trajectory file. `critic_max_turns` caps the
    # sub-agent's loop in case the model never calls `report_verdict`;
    # on hit we force-emit a fail verdict rather than recurse forever.
    # `max_critique_iters` above is now the planner-side cap on how many
    # times the planner spawns the sub-agent per run (one CriticAgent
    # invocation == one revise round).
    critic_max_turns: int = 10

    # v2.7.3 hotfix (2026-04-26) — cap how many slide PNGs the critic
    # may pull into a single turn via `read_slide_render`. The 153K-token
    # context blow-up on longcat-next-2026.pdf came from a 13-slide deck
    # being fetched in parallel and the JSON-encoded base64 leaking into
    # subsequent turns as plain `tool` messages. We now deliver each
    # PNG as a real vision content block on a follow-up user message,
    # but the per-turn cap defends against the model still trying to
    # haul the whole deck in one go (Qwen-VL-Max bills ~1k image tokens
    # each; 4 per turn ≈ 4k image tokens + the small ack JSONs).
    # Surplus calls return a "deferred" ack and re-queue for the next
    # turn so the model learns to chunk its inspection.
    critic_max_images_per_turn: int = 4

    # Extended thinking — applies to BOTH backends (Anthropic uses thinking=
    # block; OpenAI-compat uses extra_body.reasoning.max_tokens for OpenRouter
    # unified format). budget=0 disables thinking entirely.
    planner_thinking_budget: int = 10000
    critic_thinking_budget: int = 10000
    # Anthropic-only: interleaved-thinking-2025-05-14 beta header. No-op for
    # OpenAI-compat backends (reasoning is naturally per-turn there).
    enable_interleaved_thinking: bool = True

    # v2.4.2 — bundled OFL fonts. Flat `family → filename` for back-compat
    # with every downstream lookup (`settings.fonts.get(family)`). Families
    # with a single file ship their "-Variable.ttf" wght-axis master so
    # CSS `font-weight` picks the right cut; the legacy CJK `-Bold.otf`
    # entries are kept for the PSD/SVG/PNG path where PIL doesn't honour
    # the OpenType wght axis.
    #
    # When you add a family here, also extend the typography section of
    # `prompts/planner.md` so the planner knows it exists.
    fonts: dict[str, str] = field(default_factory=lambda: {
        # Legacy CJK bold (used by PSD/PPTX/PNG rasterization via PIL).
        "NotoSansSC-Bold": "NotoSansSC-Bold.otf",
        "NotoSerifSC-Bold": "NotoSerifSC-Bold.otf",
        # Variable CJK — all weights in one file, ideal for HTML/SVG.
        "NotoSansSC": "NotoSansSC-Variable.ttf",
        "NotoSerifSC": "NotoSerifSC-Variable.ttf",
        # Latin — all variable masters, weights via `font-weight` CSS.
        "Inter": "Inter-Variable.ttf",
        "IBMPlexSans": "IBMPlexSans-Variable.ttf",
        "JetBrainsMono": "JetBrainsMono-Regular.ttf",  # variable wght axis
        "PlayfairDisplay": "PlayfairDisplay-Variable.ttf",
    })
    default_text_font: str = "NotoSansSC-Bold"
    default_title_font: str = "NotoSerifSC-Bold"

    @property
    def llm_backend(self) -> str:
        """Legacy convenience field — describes the underlying credential
        path, NOT the active provider per-role (use `planner_provider` /
        `critic_provider` for that)."""
        return "openrouter" if self.anthropic_base_url else "anthropic"


def load_settings() -> Settings:
    # Gemini key is now optional — only required when image_provider
    # resolves to `gemini`. We validate lazily inside the backend so a
    # seedream-only user doesn't need to set it. The startup check below
    # used to fail-loud here; that contract moved into
    # `GeminiImageBackend.__init__` (image_backend.py).
    gemini = os.getenv("GEMINI_API_KEY", "").strip()

    or_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    ant_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    base_url_override = os.getenv("ANTHROPIC_BASE_URL", "").strip() or None

    # Anthropic SDK credential resolution — same as before. The OpenAI-compat
    # backend may use the same key (when in OR mode) or its own (next block).
    if or_key:
        api_key = or_key
        base_url = OPENROUTER_BASE_URL_ANTHROPIC
        anthropic_default_planner = DEFAULT_PLANNER_MODEL
        anthropic_default_critic = DEFAULT_CRITIC_MODEL
    elif ant_key:
        api_key = ant_key
        base_url = base_url_override
        anthropic_default_planner = ANTHROPIC_FALLBACK_PLANNER
        anthropic_default_critic = ANTHROPIC_FALLBACK_CRITIC
    else:
        raise RuntimeError(
            "No LLM credential — set OPENROUTER_API_KEY (preferred, powers both "
            "providers) or ANTHROPIC_API_KEY in .env"
        )

    # OpenAI-compat backend: defaults to OpenRouter using the same key. User
    # can override to point at native Moonshot / DeepSeek / vLLM via env.
    oai_key = os.getenv("OPENAI_COMPAT_API_KEY", "").strip() or (or_key or None)
    oai_base = os.getenv("OPENAI_COMPAT_BASE_URL", "").strip() or OPENROUTER_BASE_URL_OPENAI

    planner_model = os.getenv("PLANNER_MODEL", "").strip() or anthropic_default_planner
    critic_model = os.getenv("CRITIC_MODEL", "").strip() or anthropic_default_critic
    planner_provider = _parse_provider(os.getenv("PLANNER_PROVIDER", "auto"))
    critic_provider = _parse_provider(os.getenv("CRITIC_PROVIDER", "auto"))

    # v2.4 enhancer resolution — default is Opus 4.7, but if the user only
    # has ANTHROPIC_API_KEY (no OpenRouter), strip the `anthropic/` prefix
    # so the stock Anthropic endpoint accepts the model id.
    if or_key:
        enhancer_default = DEFAULT_ENHANCER_MODEL
    else:
        enhancer_default = ANTHROPIC_FALLBACK_ENHANCER
    enhancer_model = os.getenv("ENHANCER_MODEL", "").strip() or enhancer_default
    enhancer_provider = _parse_provider(os.getenv("ENHANCER_PROVIDER", "auto"))
    enhancer_budget = _parse_int_env("ENHANCER_THINKING_BUDGET", 10000)
    # SKIP_PROMPT_ENHANCER=1 disables the stage at settings-load time;
    # the `--skip-enhancer` CLI flag also toggles this per-run.
    skip_enhancer_env = os.getenv("SKIP_PROMPT_ENHANCER", "").strip() in (
        "1", "true", "True", "yes",
    )
    enable_prompt_enhancer = not skip_enhancer_env

    # v2.8.0 ClaimGraph extractor resolution — same fallback rule as the
    # enhancer (drop OpenRouter prefix when only ANTHROPIC_API_KEY is set).
    if or_key:
        claim_graph_default = DEFAULT_CLAIM_GRAPH_MODEL
    else:
        claim_graph_default = ANTHROPIC_FALLBACK_CLAIM_GRAPH
    claim_graph_model = (
        os.getenv("CLAIM_GRAPH_MODEL", "").strip() or claim_graph_default
    )
    claim_graph_provider = _parse_provider(
        os.getenv("CLAIM_GRAPH_PROVIDER", "auto"),
    )
    claim_graph_max_turns = _parse_int_env("CLAIM_GRAPH_MAX_TURNS", 15)
    claim_graph_budget = _parse_int_env("CLAIM_GRAPH_THINKING_BUDGET", 8000)
    no_claim_graph_env = os.getenv("NO_CLAIM_GRAPH", "").strip() in (
        "1", "true", "True", "yes",
    )
    enable_claim_graph = not no_claim_graph_env

    if or_key:
        ingest_default = "qwen/qwen-vl-max"
    else:
        ingest_default = "claude-sonnet-4-7"
    ingest_model = os.getenv("INGEST_MODEL", "").strip() or ingest_default

    planner_budget = _parse_int_env("PLANNER_THINKING_BUDGET", 10000)
    critic_budget = _parse_int_env("CRITIC_THINKING_BUDGET", 10000)
    critic_max_turns_env = _parse_int_env("CRITIC_MAX_TURNS", 10)
    critic_max_images_env = _parse_int_env("CRITIC_MAX_IMAGES_PER_TURN", 4)
    interleaved = os.getenv("ENABLE_INTERLEAVED_THINKING", "1").strip() not in (
        "0", "false", "False", "no", "",
    )
    ingest_timeout = float(_parse_int_env("INGEST_HTTP_TIMEOUT", 600))

    # v2.5 image-backend resolution. Default model is seedream via
    # OpenRouter; `image_provider` lets the user pin a backend even when
    # auto-detection would pick the other one (e.g. running an internal
    # mirror that serves a `gemini-*` slug from a non-Google endpoint).
    image_model_env = os.getenv("IMAGE_MODEL", "").strip()
    image_provider_env = _parse_image_provider(os.getenv("IMAGE_PROVIDER", "auto"))

    section_policy = _parse_section_policy(os.getenv("SECTION_NUMBER_POLICY", "renumber"))

    return Settings(
        anthropic_api_key=api_key,
        anthropic_base_url=base_url,
        openrouter_api_key=or_key or None,
        openai_compat_api_key=oai_key,
        openai_compat_base_url=oai_base,
        gemini_api_key=gemini,
        planner_model=planner_model,
        critic_model=critic_model,
        planner_provider=planner_provider,
        critic_provider=critic_provider,
        enhancer_model=enhancer_model,
        enhancer_provider=enhancer_provider,
        enhancer_thinking_budget=enhancer_budget,
        enable_prompt_enhancer=enable_prompt_enhancer,
        claim_graph_model=claim_graph_model,
        claim_graph_provider=claim_graph_provider,
        claim_graph_max_turns=claim_graph_max_turns,
        claim_graph_thinking_budget=claim_graph_budget,
        enable_claim_graph=enable_claim_graph,
        ingest_model=ingest_model,
        ingest_http_timeout=ingest_timeout,
        planner_thinking_budget=planner_budget,
        critic_thinking_budget=critic_budget,
        critic_max_turns=critic_max_turns_env,
        critic_max_images_per_turn=critic_max_images_env,
        enable_interleaved_thinking=interleaved,
        **({"image_model": image_model_env} if image_model_env else {}),
        image_provider=image_provider_env,
        section_number_policy=section_policy,
    )


def _parse_provider(raw: str) -> ProviderChoice:
    raw = (raw or "").strip().lower()
    if raw in ("auto", "anthropic", "openai_compat"):
        return raw  # type: ignore[return-value]
    if raw in ("openai", "openrouter", "moonshot", "deepseek", "kimi", "doubao"):
        return "openai_compat"
    if raw in ("claude",):
        return "anthropic"
    return "auto"


def _parse_section_policy(raw: str) -> SectionNumberPolicy:
    """Normalize SECTION_NUMBER_POLICY env. Falls back to "renumber" on
    anything unrecognised so a typo never stops a run."""
    raw = (raw or "").strip().lower()
    if raw in ("renumber", "strip", "preserve"):
        return raw  # type: ignore[return-value]
    if raw in ("auto", "default", ""):
        return "renumber"
    if raw in ("none", "off", "drop"):
        return "strip"
    if raw in ("keep", "noop", "as-is"):
        return "preserve"
    return "renumber"


def _parse_image_provider(raw: str) -> ImageProviderChoice:
    """Normalize IMAGE_PROVIDER env. Accepts a few friendly aliases
    (`google`, `nbp` → gemini; `or`, `seedream`, `bytedance` → openrouter)
    so users don't have to remember the canonical token."""
    raw = (raw or "").strip().lower()
    if raw in ("auto", "gemini", "openrouter"):
        return raw  # type: ignore[return-value]
    if raw in ("google", "nbp", "imagen"):
        return "gemini"
    if raw in ("or", "openai_compat", "seedream", "bytedance", "doubao"):
        return "openrouter"
    return "auto"


def resolve_font(family: str | None, weight: str = "regular",
                 settings: "Settings | None" = None) -> Path | None:
    """Resolve ``(family, weight)`` to an on-disk font path, or None.

    v2.4.2 forward-compat API. Most consumers still use the flat
    ``settings.fonts.get(family)`` lookup; this helper wraps it with two
    niceties:
    - Accepts legacy suffix-encoded names (``"NotoSansSC-Bold"`` →
      family=``NotoSansSC``, weight=``bold``). Downstream code can move
      to the ``(family, weight)`` pair incrementally.
    - Falls back to the plain-family key when no weight-specific file is
      registered (e.g. ``resolve_font("Inter", weight="bold")`` returns
      ``Inter-Variable.ttf`` because the variable TTF covers all cuts).
    - Returns ``None`` (not an exception) when nothing matches.
    """
    if not family:
        return None
    cfg = settings or load_settings()
    registry = cfg.fonts

    family_clean = family.strip()
    if not family_clean:
        return None

    # Legacy "Family-Weight" shortcut — if the exact key is registered,
    # prefer it (back-compat with existing trajectories).
    if family_clean in registry:
        return cfg.fonts_dir / registry[family_clean]

    # Split trailing -Bold / -Regular / -Medium etc. onto the weight axis.
    if "-" in family_clean:
        base, _, suffix = family_clean.rpartition("-")
        if suffix.lower() in {"regular", "bold", "medium", "light",
                              "thin", "black", "semibold", "extralight"}:
            weight = suffix.lower()
            family_clean = base

    weighted_key = f"{family_clean}-{weight.capitalize()}" if weight != "regular" else family_clean
    for candidate in (weighted_key, family_clean):
        path = registry.get(candidate)
        if path:
            return cfg.fonts_dir / path
    return None


def _parse_int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# ───────────────────────── Poster templates (v2.3) ─────────────────────
# Canonical canvas presets for academic poster venues. Users pass
# `--template <name>` at the CLI and the runner injects the resolved
# canvas into the brief prologue (like the `Attached files:` block for
# ingest). No DesignSpec schema change — `canvas` stays a dict; template
# is an input-side convenience, not an output-side concept.
#
# Dimensions at 300 DPI for print-ready output. Add new entries here as
# new venues surface in dogfood. Free to override any individual field
# (w_px / h_px / dpi / aspect_ratio) — the dict shape matches
# DesignSpec.canvas verbatim.
POSTER_TEMPLATES: dict[str, dict[str, object]] = {
    "neurips-portrait": {
        "w_px": 1536, "h_px": 2048, "dpi": 300,
        "aspect_ratio": "3:4", "color_mode": "RGB",
    },
    "cvpr-landscape": {
        "w_px": 2048, "h_px": 1536, "dpi": 300,
        "aspect_ratio": "4:3", "color_mode": "RGB",
    },
    "icml-portrait": {
        "w_px": 1536, "h_px": 2048, "dpi": 300,
        "aspect_ratio": "3:4", "color_mode": "RGB",
    },
    # ISO A0 at 300 DPI: 841 mm × 1189 mm ≈ 9933 × 14043 px (too heavy
    # for most planners). We use a 1:√2 preset at 1/4 linear scale that
    # still prints crisply on a standard A0 plotter.
    "a0-portrait": {
        "w_px": 2378, "h_px": 3366, "dpi": 300,
        "aspect_ratio": "1:1.414", "color_mode": "RGB",
    },
    "a0-landscape": {
        "w_px": 3366, "h_px": 2378, "dpi": 300,
        "aspect_ratio": "1.414:1", "color_mode": "RGB",
    },
}


def resolve_template(name: str | None) -> dict[str, object] | None:
    """Return the canvas dict for a registered template name, or None
    if `name` is None / unknown. Case-insensitive + hyphen-or-underscore
    tolerant so `--template A0_Portrait` works."""
    if not name:
        return None
    key = name.strip().lower().replace("_", "-")
    return POSTER_TEMPLATES.get(key)


def available_templates() -> list[str]:
    """Sorted list of registered template names — used by CLI --help."""
    return sorted(POSTER_TEMPLATES.keys())
