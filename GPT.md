# GPT / Codex Maintainer Guide

This is the GPT/Codex handoff file for OpenDesign. It is meant to be read at
the start of a new development session, before editing code. It summarizes the
project shape, the invariants that matter, and the places where docs may drift.

## First Read

Start here, in order:

1. `README.md` for the public product surface and latest status note.
2. `docs/README.md` for the knowledge-base map.
3. `docs/ARCHITECTURE.md` for the module map and data flow.
4. `docs/ROADMAP.md` for current priorities.
5. `docs/DECISIONS.md` before reopening an architectural choice.
6. `docs/GOTCHAS.md` before debugging a weird runtime failure.
7. `open_design/schema.py`, `open_design/config.py`, and `open_design/runner.py`
   as the code-level source of truth.

If docs disagree with code, the code wins and the doc is a bug. Fix docs as part
of the change when the drift is relevant. This checkout already has some
historical drift: older docs may mention `Design-Agent`, old smoke counts, or
Claude-only routing. The actual repo is `OpenDesign`, smoke is 20 checks, and
planner/critic/enhancer go through the multi-provider `LLMBackend`.

## Product Thesis

OpenDesign is both:

- an open-source, terminal-first conversational design agent; and
- a training-data pipeline for layered design generation.

Every successful run should produce a user artifact and a distillation-ready
`DistillTrajectory`. Do not treat trajectories as incidental logs. They are one
of the core products.

Supported artifact types:

- `poster`: PSD + SVG + editable HTML + preview PNG over a text-free background.
- `landing`: self-contained HTML one-pager + preview PNG.
- `deck`: native PPTX + per-slide PNGs + grid preview.

The north star is paper2any: attach a paper / DOCX / PPTX / Markdown / image
bundle, ingest real content, then generate poster, landing, or deck with
editable output.

## Current Checkout Snapshot

As of this checkout, the code includes the v2 training-data schema, v2.3
paper2any polish, and v2.4 work that may be ahead of parts of the docs:

- `DistillTrajectory` is the persisted trajectory model.
- `ToolResultRecord` is lean by design: no path leakage, no `summary`, no
  `next_actions`.
- Intermediate artifacts are versioned under `out/runs/<run_id>/composites/`
  and surfaced through `out/runs/<run_id>/final/`.
- `PromptEnhancer` exists in `open_design/agents/prompt_enhancer.py` and is
  wired in `PipelineRunner.run()` before `PlannerLoop`.
- `--skip-enhancer` exists for one-shot CLI A/B runs.
- The font registry in `config.py` includes Noto SC, Inter, IBM Plex Sans,
  JetBrains Mono, and Playfair Display families.
- `prompts/prompt_enhancer.md` may still contain older font availability text;
  verify against `config.py` before changing prompt behavior.

Near-term likely priorities from the roadmap and code state:

- reconcile roadmap/docs with the already-wired v2.4 prompt enhancer and fonts;
- add or improve enhancer regression tests if the feature needs hardening;
- finish interactive image manipulation / preview mode if still desired;
- investigate the poster revise-loop degradation noted in v2.3 dogfood;
- keep paper2any figure/table quality high, especially for landings and decks.

## Architecture In One Pass

User entry points:

- `open_design/cli.py`: `chat`, `run`, and `apply-edits`.
- `open_design/chat.py`: REPL sessions and slash commands.
- `open_design/apply_edits.py`: edited poster/landing HTML back to a fresh run.

Core orchestration:

- `PipelineRunner` owns run dirs, `ToolContext`, prompt enhancement, planner
  execution, outcome derivation, cost estimate, and trajectory writing.
- `PromptEnhancer` rewrites raw briefs into structured planner briefs. It has no
  tools and should fail open to raw brief pass-through.
- `PlannerLoop` owns the tool-use conversation and `agent_trace`.
- `Critic` branches by artifact type: poster uses vision, landing and deck use
  text-only rubrics.
- `ToolContext` is the per-run blackboard. It is fresh per chat turn.

Tool action space is registered in `open_design/tools/__init__.py`. Keep
`switch_artifact_type` first. The 11 tools are:

1. `switch_artifact_type`
2. `ingest_document`
3. `propose_design_spec`
4. `generate_background`
5. `generate_image`
6. `render_text_layer`
7. `edit_layer`
8. `fetch_brand_asset`
9. `composite`
10. `critique`
11. `finalize`

## Data Contract Rules

`open_design/schema.py` is the single source of truth.

Important runtime models:

- `DesignSpec`
- `LayerNode`
- `CritiqueResult`
- `CompositionArtifacts`

Important persisted training models:

- `DistillTrajectory`
- `AgentTraceStep`
- `ToolResultRecord`
- `ThinkingBlockRecord`
- `TrainingMetadata`

Do not re-add product/debug state to `DistillTrajectory`. Recover product
details from `agent_trace` and disk artifacts instead. Keep local file paths out
of trajectory JSON. Keep tool results lean enough for policy training.

When changing schema:

1. edit `open_design/schema.py`;
2. update `docs/DATA-CONTRACT.md`;
3. update `runner.py` metadata handling if needed;
4. add a dated note in `docs/DECISIONS.md` for non-obvious or breaking changes;
5. run smoke.

## Artifact Invariants

Poster:

- Backgrounds must be text-free. Text is rendered separately.
- Coordinates are top-left origin, pixel units.
- `generate_background` is poster-only and takes `safe_zones`.
- `render_text_layer` and `edit_layer` produce versioned layer PNGs.
- Composite writes PSD, SVG, editable HTML, and preview.
- SVG text should remain real vector text with embedded/subset fonts.
- Poster critique is vision-based.

Landing:

- HTML is the primary artifact and must be self-contained.
- Text stays native HTML; do not rasterize copy or equations.
- Use one of the bundled design systems: `minimalist`, `editorial`,
  `claymorphism`, `liquid-glass`, `glassmorphism`, `neubrutalism`.
- Paper landings should prefer ingested figures over NBP imagery.
- Tables should render as real HTML tables with real rows, never empty
  placeholders.
- KaTeX is self-hosted and gated on math delimiters.
- CTA layers, auto-nav, reveal-on-scroll, and semantic header/main/footer are
  part of the expected surface.
- Landing critique is text-only; Pillow previews are not authoritative enough
  for vision grading.

Deck:

- PPTX is the primary artifact.
- Text should be native PowerPoint TextFrames.
- Tables should be native PPTX tables.
- `LayerNode.speaker_notes` should populate presenter notes.
- Keep imagery coherent through a repeated style prefix in `composition_notes`
  and `generate_image` prompts.
- Deck critique is text-only on the slide tree.

Paper ingest:

- If `Attached files:` is present, planner should call `ingest_document` first.
- Avoid rasterizing PDFs to shrink them; that destroys embedded figure
  extraction.
- Prefer native paper figures/tables for scientific content. NBP is decorative
  or ambient unless the source truly lacks a usable asset.
- If table transcription fails, omit the table instead of emitting `rows=[]`.
- Watch large PDF size caps and OpenRouter/Anthropic timeout behavior.

## LLM And Provider Rules

All planner, critic, and enhancer calls should go through `LLMBackend`.
All image-generation calls should go through `ImageBackend` (v2.5 — was
Gemini-only through v2.4).

Default routing in `config.py`:

- planner: `deepseek/deepseek-v3.2-exp` (164k ctx, sparse-attn — was
  Kimi K2.6 through v2.5; switched 2026-04-25 after Kimi stalled out
  with `terminal_status=max_turns` on paper2deck dogfood)
- critic: `qwen/qwen-vl-max` (multimodal — was Kimi K2.6 through v2.5;
  switched 2026-04-25 so critic can grade rendered output across all
  artifact types, not just poster)
- enhancer: `moonshotai/kimi-k2.6` (was Opus 4.7 in v2.4.1; cost-cut 2026-04-25)
- image generation: `bytedance-seed/seedream-4.5` via OpenRouter
  (was `gemini-3-pro-image-preview` through v2.4; cost-cut 2026-04-25)
- ingest VLM: `qwen/qwen-vl-max` via OpenRouter when available

Image-backend module (`open_design/image_backend.py`) mirrors `LLMBackend`:

- `ImageBackend` Protocol with one method `generate(prompt, aspect_ratio,
  image_size) -> ImageResult`.
- `GeminiImageBackend` wraps `google.genai`. Selected when `image_model`
  starts with `gemini-` / `imagen-`. Requires `GEMINI_API_KEY`.
- `OpenRouterImageBackend` POSTs to chat/completions with
  `modalities=["image","text"]` + `image_config={aspect_ratio, image_size}`.
  Selected for everything else. Reuses `OPENROUTER_API_KEY`.
- `make_image_backend(settings)` is the only entry point tools should use.
- No silent cross-provider fallback. Backend errors raise
  `ImageGenerationError(message, category)`; tools catch and convert to
  `obs_error(...)` so the planner sees a typed failure.

Useful env vars:

- `GEMINI_API_KEY`
- `OPENROUTER_API_KEY`
- `ANTHROPIC_API_KEY`
- `OPENAI_COMPAT_API_KEY`
- `OPENAI_COMPAT_BASE_URL`
- `PLANNER_MODEL`, `CRITIC_MODEL`, `ENHANCER_MODEL`
- `PLANNER_PROVIDER`, `CRITIC_PROVIDER`, `ENHANCER_PROVIDER`
- `PLANNER_THINKING_BUDGET`, `CRITIC_THINKING_BUDGET`,
  `ENHANCER_THINKING_BUDGET`
- `IMAGE_MODEL`, `IMAGE_PROVIDER` (`auto` | `gemini` | `openrouter`)
- `SKIP_PROMPT_ENHANCER=1`

Do not hard-code Claude-only behavior into new code. Claude references in older
docs are often historical; provider neutrality is now an architectural
commitment.

OpenRouter URL trap:

- Anthropic SDK base URL: `https://openrouter.ai/api` without `/v1`.
- OpenAI-compatible base URL: `https://openrouter.ai/api/v1`.

## Development Workflow

Common commands:

```bash
uv sync
uv run python -m open_design.smoke
uv run python -m open_design.cli
uv run python -m open_design.cli run "brief..."
uv run python -m open_design.cli run --skip-enhancer "brief..."
uv run python -m open_design.cli run --from-file paper.pdf --template neurips-portrait "brief..."
uv run python scripts/export_sft_jsonl.py --min-reward 0.85 --terminal-status pass
```

Smoke is no-API and should be the default verification after code changes that
touch schema, tools, renderers, prompts that affect tool use, apply-edits,
trajectory serialization, or the registry. It writes generated artifacts under
`out/smoke*`.

For docs-only changes, smoke is usually not necessary. Check the diff instead.

API dogfood runs can be slow and expensive. Do them when the task requires
end-to-end validation or the user explicitly asks.

## Adding Or Changing Features

Adding a tool:

1. add `open_design/tools/<tool>.py`;
2. register JSON schema and handler in `open_design/tools/__init__.py`;
3. update `prompts/planner.md`;
4. update `open_design/smoke.py::check_tool_registry`;
5. update docs if the workflow changes;
6. run smoke.

Adding an artifact type:

1. extend `ArtifactType` and any `LayerKind` values;
2. add composite renderer branch;
3. add renderer module;
4. add critic rubric and `critic.py` branch;
5. decide how runner should recover product state from trace/runtime state;
6. add smoke coverage;
7. document workflow and decisions.

Changing prompt behavior:

- Check `prompts/planner.md`, `prompts/prompt_enhancer.md`, and the relevant
  critic rubric together.
- Keep prompt instructions aligned with current schema and tool schemas.
- If a prompt references available fonts, artifact paths, or tool counts, verify
  against code before editing.

Changing renderer behavior:

- Preserve editability first: native HTML/PPTX text and real SVG text matter
  more than pretty raster previews.
- Preserve versioned outputs.
- Preserve apply-edits round trip for poster and landing.

## Known Gotchas To Remember

- `.env` is loaded with `override=True`; empty shell exports should not mask it,
  but old docs mention this because it used to hurt.
- macOS may hide editable install `.pth` files; see `docs/GOTCHAS.md`.
- Gemini image bytes must be re-encoded through PIL.
- Anthropic vision has image size limits; poster critic downsizes previews.
- Figma SVG import is unreliable; browser SVG rendering is the reference.
- Cost estimates are heuristics, especially when users override models.
- `out/` and `sessions/` are generated state. Do not commit generated artifacts
  unless the user explicitly wants fixtures or examples.

## Maintainer Style

Prefer small, sympathetic changes that fit the existing architecture:

- Pydantic models over ad-hoc dict contracts.
- Handwritten tool loop over orchestration frameworks.
- Provider-neutral LLM calls over model-specific hacks.
- Real editable source files over raster-only shortcuts.
- Docs updates alongside behavior changes.

When uncertain, read `docs/DECISIONS.md` first. A lot of strange-looking choices
are intentional because they protect editability, trajectory quality, or
paper2any fidelity.
