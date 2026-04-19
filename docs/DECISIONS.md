# Decisions Log

Append-only record of non-obvious design choices and the reasoning behind them. **Don't reopen settled questions without reading the rationale here first.** When you make a new decision, add an entry at the top with the date.

Format: each entry has **Decision** (one-line), **Alternatives considered**, **Rationale**, optionally **Revisit when** (conditions that should trigger reopening).

---

## 2026-04-18 — Chat shell: `ChatSession` is a thin outer wrapper; each turn gets a FRESH `ToolContext`

**Decision**: In chat mode, each user turn that triggers generation creates a brand-new `PipelineRunner` + `ToolContext`. The outer `ChatSession` only stores `TrajectoryRef` entries (paths + summary metadata) and `ChatMessage` history, NOT carrying `ToolContext` state across turns.

**Alternatives**:
1. One long-lived `ToolContext` per session, tools append to `rendered_layers` across turns — appealing for "edit the previous title" workflows.
2. Session-level state mirror (copy prior trajectory's state into new ToolContext on each turn) — more complex, still error-prone.

**Rationale**: Per-turn isolation matches the Trajectory-as-unit-of-work principle. Each generation is self-contained: brief → DesignSpec → layers → composition → critique → finalize. If the user wants to revise the prior artifact, the planner re-proposes the spec (possibly reusing layer_ids to overwrite) — this is cheap at the current scale (~$1-4 per turn) and keeps the data model clean for SFT extraction. Inter-turn carryover lives at the CONVERSATION layer (ChatSession.trajectories + prior-trajectory context injection in the user brief), not at the ToolContext layer. `edit_layer` (v1.0 #5) will still respect this: it'll take a layer_id + diff and produce a new trajectory with that single layer replaced, not mutate an old one in-place.

**Revisit when**: we observe users consistently producing "revision chains" (5+ successive edits to same artifact) where the full per-turn regeneration cost dominates — at that point, a selective "patch only these layers" fast-path might pay off.

---

## 2026-04-19 — CORRECTION: Chat context injection must include the FULL prior DesignSpec, not just metadata

**Decision**: `chat.py._build_contextual_brief` now injects the full prior `DesignSpec` JSON (including `layer_graph` with per-layer text, font_family, font_size_px, bbox, palette, mood, composition_notes) whenever the session has a prior trajectory. Metadata-only summary (run_id + verdict + path) was insufficient.

**This overrides** the 2026-04-18 decision (immediately below) which argued for latest-only metadata summary on token-budget grounds. That decision was wrong.

**Root-cause bug** that triggered the correction: user ran a Neural Networks poster on turn 1 (9 layers), then said "make the title bigger" on turn 2. Planner received a brief prefixed with `run_id: 20260418-231249-ede40b1a` + `type: poster` + path, but had no actual access to the prior spec (no filesystem-read tool). When forced to revise "the prior artifact" with zero visibility into its content, the planner regressed to the strongest few-shot anchor in `prompts/planner.md` (国宝回家) and produced a poster with palette `['#1a0f0a','#fafafa','#a02018','#c9a45a']`, mood `['oriental epic',...]`, layers `国宝回家 / National Treasures / 归途` — verbatim from the few-shot example. Cost: $1.44 wasted on generating the wrong poster. Session: `session_20260418-231218_f285acbc`.

**Fix** (commit pending):
1. `chat.py`: load the prior trajectory's `design_spec` from disk, dump it as indented JSON inside a ```` ```json ```` block in the contextual brief, alongside a "COPY this, don't invent" instruction.
2. `prompts/planner.md`: add an explicit guard — when the brief prefix contains a `### Prior DesignSpec` block, that IS the starting point; few-shot anchors (国宝回家) are for FIRST-TURN briefs only.

**Rationale for the reversal**: token cost was the wrong optimization. A 9-layer spec serializes to ~3-4 KB (~1K tokens ≈ ~$0.02 extra per turn). The alternative is the planner fabricating a completely different artifact — costing a full $1-4 per wrong turn and, worse, poisoning the training-data trajectory with a spec that doesn't actually describe what the user wanted. Grounding > token-saving.

**Revisit when**: sessions accumulate long histories (>10 trajectories) where even latest-only injection becomes heavy. At that point, compact the injected spec: drop references field, trim layer_graph to `(id, kind, bbox, text, font_size_px)` per layer, drop src_path (runtime-only). For now, full spec is fine at <10 trajectories per session.

---

## 2026-04-18 — Chat context injection: summarize ONLY the latest trajectory, not the full history (SUPERSEDED 2026-04-19)

> **⚠️ This decision was wrong. Superseded by the 2026-04-19 entry above.** The token-budget argument held, but the quality impact (planner regressed to the 国宝回家 few-shot when asked to revise a Neural Networks poster) outweighed the savings by 50-100×. Kept here as record; the fix is full-spec injection.

**Decision (superseded)**: Each new user brief in a chat session is prefixed with a summary of *only the latest* `TrajectoryRef` (run_id, artifact_type, n_layers, verdict, path). Not the full `ChatSession.trajectories` list. Not the full design_spec. Just a pointer to the most recent and a decision prompt.

**Alternatives**:
1. Inject full prior-artifact design_spec (palette, mood, composition_notes, full layer_graph) — lets planner reuse those values exactly.
2. Inject all prior trajectories in the session — lets planner reference artifact #1 when making artifact #5.
3. Inject nothing — treat every turn as independent, rely on the user's brief to provide full context.

**Rationale**: Option 1 would roughly double each turn's input tokens (spec + layer_graph is ~2K tokens). Option 2 scales linearly with session length — by turn 10, we'd be wasting tokens on stale context. Option 3 makes revisions ("make the title bigger") meaningless because planner has no clue what "the title" refers to. The chosen middle path (latest-only summary) is cheap (~200 tokens), enough to distinguish revision-vs-new-artifact, and matches the natural chat UX where users primarily refer to "the thing I just made." If a user wants to reference an earlier artifact, they can say so explicitly ("like the first poster but with cyan text") and the planner can use `:history` / the session file as needed.

**Revisit when**: users consistently want to cross-reference artifacts earlier than the latest (we'd add an optional `--artifact-context=all` flag), OR when `edit_layer` lands and the planner needs more structured layer metadata in its context (at that point, inject `layer_manifest` alongside the high-level summary).

---

## 2026-04-18 — CLI subcommand split: `chat` (default) + `run` (one-shot), backward-compatible

**Decision**: `longcat-design` with no subcommand launches the chat REPL. The old one-shot behavior (single brief → single trajectory) moves to `longcat-design run "<brief>"`. Both work via the same `cli.py` with argparse subparsers.

**Alternatives**:
1. Keep one-shot as default, add `longcat-design chat` explicitly — preserves v0 CLI behavior but makes the headline v1.0 feature a secondary option.
2. Remove one-shot entirely, chat is the only interface — cleaner, but breaks automation/scripting/CI use cases and loses an easy path for batch dataset generation.
3. Two separate binaries (`longcat-design-chat` + `longcat-design-run`) — extra pyproject scripts, extra PATH clutter, no real benefit.

**Rationale**: Chat is the v1.0 product-UX headline — should be the frictionless default. One-shot is still valuable for (a) dataset generation scripts that call it in a loop, (b) CI smoke tests, (c) users who just want one artifact and are scared of a REPL. Keeping both under argparse subparsers is zero extra infra. There are no external users yet, so "breaking" the no-arg one-shot form costs nothing in adoption — and the explicit `run` subcommand is arguably clearer even in isolation.

**Revisit when**: usage telemetry (if we ever add it) shows `run` used <5% of the time AND no one complains — at that point consider removing `run` for simplicity. OR if the REPL becomes too slow to start (cold start >2s) for scripting use — in which case maybe reverse the default.

---

## 2026-04-18 — PIVOT: rebrand as LongcatDesign, reposition as open-source Claude Design alternative

**Decision**: Stop framing the project primarily as "Longcat-Next training-data pipeline / research prototype." Rename to **LongcatDesign** and ship as an **open-source product** — a terminal-first conversational design agent that is a true alternative to Claude Design (Anthropic's closed SaaS released 2026-04-18). v1.0 MVP covers three artifact types (poster / slide deck / landing page) with HTML as first-class output.

**Alternatives considered**:
1. **Stay the course** — continue v0.1 → v0.2 → v0.3 under trajectory-first framing. Rejected: after Claude Design launched, the product-form for AI design agents is validated in a way training-data emission isn't; missing the window leaves us building for a use case (Longcat-Next model training) that may not monetize user pain the way shipping a real product does.
2. **Build full SaaS clone of Claude Design** (browser UI, multi-user, Canva integration) — rejected as [VISION.md](VISION.md) / [COMPETITORS.md](COMPETITORS.md) note: we can't win on UX polish budget against Anthropic, but can win on open-source + terminal-first + open formats.
3. **Abandon trajectory emission entirely** — rejected: kept as internal session state. Enables resume / undo / optional future training-data harvesting with no product dilution.

**Rationale**: Claude Design validates the UX thesis (conversational iteration > one-shot) and reveals the market gap we can actually own: open-source, terminal-first, open output formats, self-hostable, model-agnostic. Also: rebranding to `LongcatDesign` aligns with our team's Longcat ecosystem (Longcat-Next et al.) — signals team credibility without forcing the training-data framing into the product pitch. Trajectory machinery is preserved; if the Longcat-Next team wants to harvest session data later, it's a feature-flag flip, not a refactor.

**Consequences**:
- [VISION.md](VISION.md) rewritten around product positioning.
- [ROADMAP.md](ROADMAP.md) reordered: v1.0 = MVP launch; old v0.x items (multi-image insets, real PSD type layer, Brand Kit) become v1.1-v1.5.
- [DATA-CONTRACT.md](DATA-CONTRACT.md) carries a banner: schema preserved, no longer the product — see its header for details.
- [COMPETITORS.md](COMPETITORS.md) needs partial revision (flagged for rewrite); differentiation-vs-Claude-Design section is newly relevant.
- [V1-MVP-PLAN.md](V1-MVP-PLAN.md) (new doc) is the single-page shipping plan for v1.0.
- Package will rename `design_agent` → `longcat_design` (pending user confirmation; tracked in todo).

**Revisit when**: (a) v1.0 ships and we have real adoption numbers — if GitHub stars/active users stay in low dozens after 3 months, reconsider positioning; (b) Anthropic open-sources Claude Design or exposes a "layered output" API — would shift the competitive landscape; (c) Longcat-Next team explicitly needs the training-data pipeline to launch — can reactivate with config flag without abandoning product.

---

## 2026-04-18 — OpenRouter as primary LLM backend (with stock Anthropic fallback)

**Decision**: Support both `OPENROUTER_API_KEY` and `ANTHROPIC_API_KEY` in env; OpenRouter takes precedence when both are set. Anthropic Python SDK is used either way (OpenRouter exposes Anthropic-compatible `/messages`).

**Alternatives**:
- Anthropic only — simpler, but locks the user to one billing relationship + the user's account had credit issues.
- OpenAI SDK against OpenRouter's OpenAI-compat endpoint — would require rewriting `planner.py` (different tool_use protocol).
- LiteLLM or similar abstraction layer — extra dependency, extra abstraction, no concrete benefit since OpenRouter Anthropic-format works.

**Rationale**: Path of least code change (just `base_url` swap on the existing Anthropic SDK), preserves the entire tool-use protocol verbatim, gives the user a single key for many providers + per-call cost reporting, removes the credit-balance failure mode.

**Revisit when**: OpenRouter goes down or changes their Anthropic-compat endpoint behavior; or Longcat-Next is hostable and we want to point planner/critic at our own model.

---

## 2026-04-18 — `load_dotenv(override=True)`

**Decision**: `.env` always wins over shell-exported env vars.

**Rationale**: Shells (especially zsh with random `.zshrc` lines or keychain integration) can export empty values for `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` that silently mask `.env`. We hit this twice in one session. Project-local `.env` is the explicit source of truth for this project's config; shell env is for the user's defaults. `.env` wins.

**Revisit when**: Never. This is the right default for project-local config.

---

## 2026-04-18 — Gemini SDK output: always re-encode through PIL

**Decision**: After `part.as_image().save()`, force a Pillow `Image.open(BytesIO(inline_data.data)).save(path, format='PNG')` re-encode regardless of what the SDK returned.

**Rationale**: `genai.Image.save("foo.png")` writes the raw `inline_data.data` bytes (which are JPEG from Gemini's side) and just names the file `.png`. Downstream tooling (psd-tools embedding, browsers parsing the SVG `<image>` tag) checks magic bytes, gets confused. Re-encoding through PIL guarantees the file format matches the file extension.

**Revisit when**: Gemini SDK starts returning true PNG bytes consistently (would need to verify across model variants).

---

## 2026-04-17 — v0 = Plan B (PSD pixel layers + SVG vector text), NOT real PSD type layer

**Decision**: In v0, every text element is rendered to a transparent RGBA PNG (Pillow) and embedded into the PSD as a *named pixel layer*. The SVG carries the *real* vector text via `<text>` elements with embedded subsetted-WOFF2 fonts. Photoshop's true `TypeLayer` is deferred to v0.2+.

**Alternatives**:
- Plan A — real PSD type layer (`psd-tools` writes a `TypeTool` block)
- Plan C — only output SVG, skip PSD entirely

**Rationale**: `psd-tools` real type layer support is brittle (font matching across systems is non-deterministic, OpenType feature support is partial, and the API is poorly documented). Getting it right would have eaten 1-2 weeks before the first end-to-end run. SVG with embedded fonts gives true editability *now* with zero font-matching risk. PSD as named pixel layers gives Photoshop users the right *layer structure* (move/resize/order/blend works) at the cost of needing to retype text — acceptable v0 compromise.

**Revisit when**: v0.2 work begins; or when a user blocks on "I need to edit Chinese text directly in Photoshop without re-typing."

---

## 2026-04-17 — Anthropic SDK + handwritten tool loop, NOT LangGraph / CrewAI / etc.

**Decision**: The planner's tool-use loop is a hand-written ~150-line `PlannerLoop` class that calls `client.messages.create(..., tools=[...])` directly and threads `tool_result` messages back manually.

**Alternatives**: LangGraph, CrewAI, AutoGen, Anthropic's `beta.tools` helpers, etc.

**Rationale**: The trajectory IS the product. We need byte-exact replayable traces with paired `tool_use_id` ↔ `tool_result` entries, per-turn token/cost tracking, and the ability to add custom step types (`design_spec`, `critique`) at semantic boundaries. High-level frameworks abstract those mechanics away and make trajectory emission painful. Hand-written loop = ~150 lines, full control, easy to audit. The trade-off is that we re-implement what frameworks give for free (retry, streaming, etc.), but those aren't on the v0 critical path.

**Revisit when**: We need streaming responses for UX, OR when we want to run multiple planners in parallel and a framework's worker-pool abstractions would help.

---

## 2026-04-17 — `propose_design_spec` is a tool, NOT free-form text

**Decision**: The planner submits its initial DesignSpec via a `propose_design_spec` tool call (with the spec as JSON in `tool_args.design_spec`), not as a JSON blob inside a free-form `text` block.

**Alternatives**: Have the planner emit the spec as a markdown JSON code block in its first response, parse it out with regex.

**Rationale**: Two benefits. (a) Pydantic validation happens cleanly at the tool boundary; bad JSON returns a structured `ToolObservation{status:"error"}` the planner can react to. (b) The trace records a clean `tool_call` ↔ `tool_result` ↔ `design_spec` (with `spec_snapshot`) sequence — downstream extractors don't have to parse free-form text to find the spec.

**Revisit when**: We add streaming + want the planner to start spec-emission incrementally before all fields are known.

---

## 2026-04-17 — Trajectory ownership: runner builds it, planner doesn't see it

**Decision**: `agent_trace` is owned by the `PlannerLoop` instance and accumulated turn-by-turn. `critique_loop` lives in `ctx.state["critique_results"]` and is appended by the `critique` tool. The runner stitches them together with everything else only at finalize-time. The planner LLM never sees trajectory state.

**Alternatives**: Pass the trajectory-so-far as context to the planner each turn so it can self-reflect on its history.

**Rationale**: Keeps the planner's working context lean (essential because tool definitions + spec + last few results already eat 30-60K tokens per turn). The planner doesn't need to read its own trace to make good decisions — the recent assistant + tool messages already give it the relevant short-term context. Adding the trajectory as context would balloon cost AND risk recursive reasoning issues.

**Revisit when**: We have a clear failure mode where the planner forgets a constraint from many turns ago that's not in its recent context.

---

## 2026-04-17 — Background generation: hard-enforce text-free via prompt suffix

**Decision**: `tools/generate_background.py` always appends the literal sentence `"No text, no characters, no lettering, no symbols, no logos, no watermarks."` to the planner's prompt before sending to Gemini. The Gemini SDK has no native `negative_prompt` parameter.

**Alternatives**: Trust the planner to include the suffix (planner.md already instructs it); use a negative-prompt parameter (doesn't exist for `gemini-3-pro-image-preview`); post-OCR check + retry.

**Rationale**: Trust-but-verify isn't enough — the entire pipeline assumes background carries zero text. A planner regression that drops the suffix would silently corrupt training data (model would learn to bake text into background). Cheap belt-and-suspenders enforcement at the tool boundary. Post-OCR check is v0.2+ tightening.

**Revisit when**: Gemini exposes a real `negative_prompt`; OR we want to allow some backgrounds to contain text (e.g., a poster of a sign, where the sign text is intentional).

---

## 2026-04-17 — SVG fonts: subset to used glyphs only

**Decision**: Use `fonttools.subset` to extract only the glyphs actually used in this poster's text, encode as WOFF2, embed via `data:font/woff2;base64,...` in `<defs><style>@font-face{...}</style></defs>`.

**Alternatives**: Embed the full Noto fonts (~16-24 MB each), reference by URL, rely on system-installed fonts.

**Rationale**: Full embed would produce 40+ MB SVG files per poster. URL references break self-containment (SVG won't render correctly when emailed/shared). System fonts break consistency (different machines, different rendering). Subsetted WOFF2 embedded inline gives self-contained SVGs at ~10-30 KB per font (vs 16-24 MB unsubsetted). The tradeoff: editing text to add new characters not in the original may show as fallback glyphs — see [GOTCHAS.md](GOTCHAS.md) "SVG character set drift" and [ROADMAP.md](ROADMAP.md) v0.1 rerender command.

**Revisit when**: Users frequently edit text content post-export and hit the character-drift issue more often than they hit the file-size issue.

---

## 2026-04-17 — Critic: max 2 iterations, hard cap

**Decision**: `Settings.max_critique_iters = 2`. Critic prompt forbids `verdict: "revise"` past iteration 2 (forces `fail`). Runner doesn't restart on `fail` — partial output still counts as a valid trajectory.

**Alternatives**: No cap; cap by total tokens; ask user to confirm continuation.

**Rationale**: Critic loops can become recursive when the model gets attached to a particular fix that doesn't help. 2 iterations is enough for one round of meaningful revision (initial → fix → final). Beyond that is diminishing returns. Partial trajectories are still useful training data — they teach the critic+planner what "stuck" looks like.

**Revisit when**: We have evidence that 3-4 iterations meaningfully improve final scores in a measurable way.

---

## 2026-04-17 — Pydantic 2 for schema, NOT free-form dicts

**Decision**: Every data shape that crosses module boundaries (especially `Trajectory`, `DesignSpec`, `LayerNode`, `ToolObservation`) is a Pydantic 2 `BaseModel`. Free-form dicts only appear inside `metadata` and `canvas` dicts where the keys can vary by run.

**Alternatives**: TypedDict, dataclasses, raw dicts.

**Rationale**: Pydantic gives runtime validation (catches bad LLM JSON early), JSON serialization with `.model_dump(mode="json")` (handles datetime, enums, nested models for free), and self-documenting schemas (you can read `schema.py` like a spec). Downstream consumers (training data loaders) get free type safety. The cost is a dependency we already have.

**Revisit when**: Pydantic becomes a perf bottleneck (won't happen at our scale).

---

## 2026-04-17 — Bundled fonts: Noto SC family (sans + serif)

**Decision**: Ship `NotoSansSC-Bold.otf` and `NotoSerifSC-Bold.otf` (Open Font License) in `assets/fonts/`. The renderer only knows about these two; unknown `font_family` strings fall back to `NotoSansSC-Bold` with a `partial` warning.

**Alternatives**: Use system fonts; ship more font weights; integrate a Font Generator tool (Lovart-style).

**Rationale**: Two fonts cover 95% of CJK + Latin needs (sans for body/Latin, serif for titles/calligraphic feel). System fonts vary by machine, breaking reproducibility. More weights add 16-24 MB each — a regular-weight pair would double bundled size for small visual benefit. Custom font generation (Lovart's "Bronze Calligraphy" feature) is v0.5+ work.

**Revisit when**: Users frequently complain that fallback to NotoSansSC ruins the design intent; OR we add the Font Generator tool.

---

## 2026-04-17 — Layer coordinates: top-left origin, pixel units

**Decision**: All `bbox` fields use top-left origin and pixel units. This matches PSD convention, Pillow convention, and SVG default.

**Alternatives**: Bottom-left origin (mathematician convention); normalized [0..1] coordinates; em / pt units.

**Rationale**: Three downstream rendering targets all use top-left pixel coords. Aligning the spec to them eliminates per-tool conversion bugs. Normalized coordinates would require knowing canvas size at every layer — possible, but adds friction. Top-left pixel is the unambiguous lingua franca.

**Revisit when**: We add print-medium output where physical units (mm/inches) matter more.

---

## Schema version history

When the trajectory schema changes, add a row here. **Never break old trajectories** — branch on `metadata.version` in downstream loaders.

| Date | Version | Change |
|---|---|---|
| 2026-04-17 | `v0` | Initial trajectory schema with 5 SFT-ready lanes. |
