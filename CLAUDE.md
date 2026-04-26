# Claude / Claude Code Maintainer Guide

Session bootstrap for Claude Code working on OpenDesign. Read this first in
every new window. This file is Claude-specific: it covers session hygiene, tool
preferences, user-specific habits, and known traps. For architecture depth and
invariants, read `GPT.md` — that file is the code-level contract and does not
need to be duplicated here.

## Read Order At Session Start

1. **`GPT.md`** — architecture, data contracts, artifact invariants, provider
   rules. Treat this as the source of truth for "how the code is supposed to
   work". It is already kept current with the v2.4.x codebase.
2. **`docs/ROADMAP.md`** — what has shipped, what is next, what is killed.
3. **`docs/DECISIONS.md`** — before reopening any architectural choice.
4. **`docs/GOTCHAS.md`** — before debugging a weird runtime failure.
5. **`open_design/schema.py`** + **`open_design/runner.py`** — only after the
   docs above, when the task touches data contracts or orchestration.

Do not skim the repo before reading `GPT.md`. It will answer most
"where does X live" questions faster than grep.

## About The User (Yaxin)

This project is owned by a researcher joining Longcat-Next who owns layered /
interleaved image-text tokenization. Details worth keeping in mind:

- **Bilingual (中文 + English)**. Reply in whichever language the user opens
  the turn in. Do not translate their phrasing back at them.
- **Research + product brain**. They care about end-user experience as much as
  the training-data yield. When there is an engineering-vs-product tradeoff,
  default to the product-facing choice and surface the tradeoff.
- **PhD-student pace**. They dogfood the tool on real papers and real
  deadlines. Regressions that only show up on real PDFs are higher priority
  than clean-but-theoretical refactors.

## Hard User Preferences (non-negotiable unless revoked)

Full text lives in `~/.claude/projects/-Users-yaxinluo-Desktop-Projects-Design-Agent/memory/`.
Summary of the feedback memories that most often bite in this repo:

- **No LangGraph / CrewAI / orchestration frameworks.** Agents are Anthropic
  SDK + handwritten tool loop. This is architectural, not preference —
  `LLMBackend` is multi-provider and any framework layer would fight it.
- **Product-thinking first.** Prefer natural-language-through-planner over
  adding CLI knobs. If a feature can be expressed as a better prompt, try
  that before adding a flag.
- **`uv`-managed project.** Use `uv sync` and `uv run`. Do **not** use `pip`,
  `python3 -m venv`, or `pip install -e .`. Breaking this wastes 10+ minutes
  rebuilding the env.
- **macOS editable-install hidden-flag trap.** After `uv sync` or `uv pip
  install -e .`, the `.pth` file may be marked `UF_HIDDEN` and imports will
  fail silently. Fix:
  ```bash
  xattr -c .venv/lib/python*/site-packages/open_design.pth && \
    chflags nohidden .venv/lib/python*/site-packages/open_design.pth
  ```
  See `docs/GOTCHAS.md` for the full story.

## Default Verification Workflow

Smoke is the default. 20 no-API checks. Always runnable:

```bash
uv run python -m open_design.smoke
```

Run smoke after any change to: schema, tools, renderers, prompts that affect
tool use, `apply-edits`, trajectory serialization, or the tool registry. Smoke
writes artifacts under `out/smoke*` — safe to delete.

For docs-only changes, skip smoke and read the diff.

API dogfood (`uv run python -m open_design.cli run "..."`) is slow and costs
real money. Only run dogfood when:
- the user explicitly asks, or
- the task requires end-to-end validation that smoke can't provide (e.g.
  provider routing changes, Gemini image-gen changes, vision-critic changes).

Always confirm cost with the user before a dogfood run that crosses ~$5.

## Tool Use Inside Claude Code

- **Prefer built-in tools over Bash.** `Read` > `cat`, `Edit` > `sed`/`awk`,
  `Write` > `echo >`. Use Bash for git, smoke, and `uv run`.
- **Parallelize independent calls.** Git status + git log + ls should go out
  in one tool-call batch. The user is on a fast loop.
- **Quality gates are available** (`/verify-change`, `/verify-security`,
  `/verify-quality`, `/verify-module`, `/gen-docs`). The rules file in
  `~/.claude/rules/ccg-skills.md` says to auto-invoke them; follow it.
- **Agents worth knowing** for this repo:
  - `Explore` / `general-purpose` for "where does X live" across the tree.
  - `everything-claude-code:python-reviewer` after non-trivial Python edits.
  - `everything-claude-code:security-reviewer` only if touching file-path
    handling, shell-outs, or user-supplied PDFs.
- **Do not invent slash commands.** Only use skills listed in the available
  skills system reminder.

## Drift To Watch For

The v2.4.x commits landed fast and some docs may lag the code. When in doubt,
trust the code:

- **Smoke count**: 52 checks as of v2.8.2 (48 baseline + 4 v2.8.2 smokes: spec-level export sanitizer / title-body alignment / closing stub detector / post-write .pptx template-default scrubber). Older docs may say 18 / 20 / 24 / 31 / 42 / 48.
- **Package name**: `open_design`. Old docs may say `design_agent` or
  `LongcatDesign`. Rename landed 2026-04-23 (commit `f337a3b`).
- **Font registry**: 8 OFL families (Noto SC, Inter, IBM Plex Sans, JetBrains
  Mono, Playfair Display, ...). Older `prompts/prompt_enhancer.md` may still
  claim 2 fonts — verify against `open_design/config.py` before editing prompt
  copy.
- **Provider routing** (v2.8, 2026-04-26):
  - planner: `moonshotai/kimi-k2.6` (env `PLANNER_MODEL`)
  - enhancer: `moonshotai/kimi-k2.6` (env `ENHANCER_MODEL`)
  - **claim_graph_extractor (v2.8.0)**: `moonshotai/kimi-k2.6` (env `CLAIM_GRAPH_MODEL`) — agent-coding model with strict JSON discipline; runs between enhancer and planner when input is a PDF.
  - **critic (v2.7.3)**: `qwen/qwen-vl-max` (env `CRITIC_MODEL`) — multimodal. **Now a forked sub-agent** (`open_design/agents/critic_agent.py`), not an inline tool. Own turn budget (`CRITIC_MAX_TURNS=10`), own trajectory (`out/<run>/trajectory/critic.jsonl`). All three artifact types (deck/landing/poster) are vision-critic'd; old `open_design/critic.py` deleted.
  - History: planner reverted to Kimi after the v2.7 provenance dogfood — DeepSeek V3.2-exp produced reward 0.88 but emitted 21 fabricated numeric tokens (caught by validator); `iter 2` it tried to cite and FABRICATED quotes (7 quote_not_in_source failures). Kimi K2.6 is the agent-coding model expected to follow the "MUST be a verbatim substring of ingest" hard constraint better. The earlier (v2.5.1) Kimi max_turns failure was on v2.5 prompt density; v2.6.1 enhancer compression + v2.7 schema clarity made the prompt leaner.
  - `anthropic/claude-opus-4-7` is one env var away for any role; use it for paper-poster (bbox geometry, per `docs/DECISIONS.md` 2026-04-22). Any code that hard-codes a model id is a bug; all LLM calls go through `LLMBackend`.
- **Image generation IS provider-swappable + auto-fallback as of v2.7.5 (2026-04-26).**
  `image_backend.py` mirrors `llm_backend.py`: `make_image_backend(settings)`
  routes by `IMAGE_MODEL` prefix (`gemini-*` / `imagen-*` → Gemini, else
  OpenRouter). **New default is `google/gemini-2.5-flash-image` via OpenRouter**
  (was `bytedance-seed/seedream-4.5` until v2.7.5b — Seedream lost its
  OpenRouter image-modality endpoint and 404'd every call; live verified
  the new default at ~$0.003/image, 13.6s). v2.7.5b also adds
  `FallbackImageBackend` wrapper: on `provider_unavailable` error the
  fallback model fires (default `openai/gpt-5-image-mini`, env
  `IMAGE_FALLBACK_MODEL`). `GEMINI_API_KEY` is now optional (validated
  lazily inside `GeminiImageBackend.__init__`). Tools call
  `make_image_backend(ctx.settings).generate(...)`; never import provider
  SDKs directly. Live verification scripts: `scripts/check_image_default.py`
  + `scripts/probe_image_models.py`.
- **Kimi vs Claude for posters**: Kimi K2.6 stalls on paper-poster bbox
  geometry (documented 2026-04-22 in `docs/DECISIONS.md`). Use Claude Opus 4.7
  for paper-poster dogfood runs until this is fixed.

## Editing Rules That Matter For This Repo

- **Trajectory is a training product.** Do not re-add `summary` /
  `next_actions` / file paths to `ToolResultRecord`. Recover anything you need
  from `agent_trace` + disk artifacts. This is a hard rule enforced by
  `docs/DECISIONS.md`.
- **Editability first.** Real SVG `<text>`, native PPTX `TextFrame`s, real
  HTML tables. Do not rasterize editable content "to make it look nicer" —
  that breaks `apply-edits` round-trip.
- **Versioned artifacts.** `composites/iter_NN/` is append-only. `final/` is a
  symlink to the newest iter. Do not clobber prior iters; DPO pair extraction
  depends on the chain.
- **Small sympathetic changes.** Pydantic over ad-hoc dicts. Handwritten tool
  loop over frameworks. Provider-neutral over model-specific branches.

## Common Commands Cheat Sheet

```bash
# Sync env (after pulling)
uv sync

# No-API smoke (always safe)
uv run python -m open_design.smoke

# REPL
uv run python -m open_design.cli

# One-shot brief run
uv run python -m open_design.cli run "poster: launch of OpenDesign v2.5"

# Paper2any
uv run python -m open_design.cli run --from-file paper.pdf \
  --template neurips-portrait "academic poster"

# Skip the v2.4.1 prompt enhancer (A/B)
uv run python -m open_design.cli run --skip-enhancer "..."

# Export SFT jsonl from passed runs
uv run python scripts/export_sft_jsonl.py --min-reward 0.85 \
  --terminal-status pass
```

## What NOT To Do

- Do not commit `out/`, `sessions/`, `.venv/`, or generated artifacts.
- Do not amend commits unless the user explicitly asks — always create a new
  commit (pre-commit hooks may have run and the state is not yours).
- Do not run `git push --force` or `git reset --hard` without confirmation.
- Do not create `README.md` or docs files the user didn't ask for.
- Do not add emojis to code or docs unless the user asks.
- Do not broaden a task's scope. A bug fix does not need a refactor; a
  one-shot feature does not need a new abstraction.

## When The User Says "给下一个 session 开个头"

They mean: update this file + the memory index if anything shifted. Do not
dump session notes into CLAUDE.md — that's what memory is for. CLAUDE.md stays
stable and points at the current code + docs.

## Current Near-Term Priorities (snapshot; re-verify against ROADMAP)

As of 2026-04-25, the most likely next slices are:

1. **Poster revise-loop degradation** — longcat-next dogfood saw 0.86 → 0.68
   across 2 critic rounds. The only artifact type still failing. Parked from
   v2.3; worth clearing before any new v2.5 scope.
2. **Enhancer regression fixtures** — v2.4.1 shipped but the three dogfood
   briefs (BAGEL / LLMSurgeon / TinyML deck) are not yet test cases.
3. **DPO pair exporter** (`scripts/export_dpo_jsonl.py`) — all substrate is on
   disk from v2.1; only the flattener is missing. Clean, low-risk addition.

Re-read `docs/ROADMAP.md` before committing to any of these — priorities
shift fast.
