# Roadmap

Versions are intentionally small and shippable. The **v1.0 launch** is the next significant milestone — everything below is ordered against that bar. Post-launch work continues in v1.x.

When you complete a version, move it to "Shipped" with the date.

---

## Shipped

### v0 — layered poster pipeline research prototype (2026-04-17 → 2026-04-18)

Original code-name: **Design-Agent**. End-to-end agent: one-line brief → text-free background (NBP) + N text layers (Pillow) → PSD with named pixel layers + SVG with real `<text>` + flat preview + structured `Trajectory` JSON. Anthropic SDK + handwritten tool loop, OpenRouter + Anthropic stock backends, 7 tools, 6-step no-API smoke test, 2-iter critic loop.

Validated with two real runs:
- 5-layer 国宝回家 poster (100 s, $1.41, score 0.86)
- 18-layer CVPR academic poster (196 s, $2.49, score 0.86)

**Positioning at the time**: research prototype for Longcat-Next training-data capture. Pivoted 2026-04-18 to **LongcatDesign** open-source product — trajectory machinery preserved as internal session state; no longer the primary pitch.

### v1.0 in-progress milestones (2026-04-18, ongoing)

**Package rename** (commit `ffd4389`): `design_agent` → `longcat_design`. pyproject `longcat-design` distribution, `longcat-design` CLI, all internal imports and references updated. Clean `pip install -e .` registers the new package; both `python -m longcat_design.cli` and `longcat-design` script entry work (modulo the macOS UF_HIDDEN trap documented in GOTCHAS).

**v1.0 #3 — `switch_artifact_type` tool + ArtifactType enum** (commit `21dc44f`): schema gains `ArtifactType` (poster / deck / landing) + `DesignSpec.artifact_type` field + new `StepType` `"artifact_switch"`. New tool registered FIRST in TOOL_SCHEMAS. Planner prompt updated with artifact-type declaration as step 1 of the workflow contract. propose_design_spec falls back to `ctx.state["artifact_type"]` when spec omits the field. 7 → 8 tools wired.

**v1.0 #4 — CLI conversational chat shell + ChatSession persistence** (commit `03517ba`): new `chat.py` (REPL, 8 slash commands: `:help`/`:save`/`:load`/`:new`/`:list`/`:history`/`:tokens`/`:export`/`:exit`) + new `session.py` (`ChatSession`/`ChatMessage`/`TrajectoryRef` + save/load/list helpers). cli.py restructured with subparsers (`chat` default + `run` one-shot). Context injection: every non-first brief prefixed with a compact summary of the latest trajectory so planner can distinguish revision-vs-new-artifact. Planner prompt grows a dedicated "revision vs new-artifact decision" section. Smoke 6 → 7 steps with ChatSession round-trip coverage.

**Third dogfood run** (2026-04-18): LongcatDesign launch poster in 5 minutes / $3.74 / 5 layers / 2-iter critique (revise 0.78 → pass 0.82). Chinese title 「龙猫设计」, English subtitle, red stamp 「开源」, bottom tagline 「对话 · 解构 · 编辑」 — all independently editable layers. Proves the architecture holds up on harder briefs (agent retried NBP 3× when safety-filter rejected first attempts).

---

## v1.0 — LongcatDesign public MVP launch (4 of 11 items done)

Three-artifact conversational design agent on GitHub, MIT-licensed, `pip install longcat-design`-able. See [V1-MVP-PLAN.md](V1-MVP-PLAN.md) for full implementation breakdown with status column.

**Must-haves for launch**:

1. ✅ **Rename** — `pyproject.toml` project name → `longcat-design`; CLI entry `longcat-design`; Python package → `longcat_design/`; docs/README branding. (commit `ffd4389`)
2. ✅ **CLI chat shell** — multi-turn conversational REPL replacing one-shot `cli.py`. 8 slash commands, readline editing, session persistence to `sessions/<id>.json`, resumable via `--resume`. (commit `03517ba`)
3. ✅ **artifact_type tool** — `switch_artifact_type(poster|deck|landing)` + `ArtifactType` enum + `DesignSpec.artifact_type` field. Declares what we're making before spec. (commit `21dc44f`)
4. **HTML renderer** — structured Tailwind CSS + inline base64 assets, self-contained `.html` file. First-class target for posters AND landing pages. Key technical differentiator vs closed SaaS. **← NEXT DEEP WORK**
5. **PPTX renderer** — `python-pptx` writing native PowerPoint type frames, one slide per deck section. Editable in PowerPoint / Keynote / Slides with no special steps.
6. **edit_layer tool** — planner-invocable: modify an existing layer's text/font/color/bbox and recompose. Unlocks `:edit` slash command (currently revisions go through full re-spec path).
7. ✅ **Conversation persistence** — each chat session saves its full message history + trajectory refs to `sessions/<id>.json`. Reload with `:load <id>` or CLI `--resume`. (shipped with #2)
8. **README + docs** — product-facing README (quickstart, showcase, install, config), KB updates for all new pieces. (partial — docs kept current; final polish pending)
9. **1 demo video** — screencast of a multi-turn session producing all 3 artifact types.

**Deferred from v1.0 to v1.x** (keeps launch scope tight):

- Multi-image insets (v0.1 original plan, now **v1.1**)
- Real PSD type layer (was v0.3, now **v1.3**)
- Brand Kit PDF parsing (was v0.6, now **v1.4**)
- Skill sedimentation (was v0.5, now **v1.5**)
- Font generator / custom fonts (was v0.7, still way later)

**Progress** (as of 2026-04-18): **4 of 11 items done** (rename + #3 + #4 + persistence included-with-#4). Remaining core work: HTML renderer (#6, biggest lift), PPTX renderer (#7), edit_layer tool (#5), landing schema (#8), README polish (#9), demo video (#10), smoke HTML/PPTX assertions (#11). Estimate ~15 h coding + 2-4 h docs/video to v1.0 tag.

---

## v1.1 — Multi-image insets

**Why**: Current biggest depth gap. Academic posters and landing-page hero sections need *figures* (architecture diagrams, photos, charts). v0 only has one full-canvas background per artifact.

**Scope** (~50 lines):
- New tool `generate_image_inset(layer_id, prompt, bbox, aspect_ratio, image_size)` — like `generate_background` but at sub-canvas bbox.
- New tool `import_local_image(layer_id, path, bbox)` — designer hands the agent a chart/photo; tool registers as image layer.
- Composite + renderers handle `kind: "image"` non-full-canvas layers (PSD / SVG / HTML).
- `planner.md` gains inset guidance: "for text-heavy or figure-required artifacts, use `generate_image_inset` for focal charts/diagrams rather than baking into background."

---

## v1.2 — Landing-page refinement: interactive HTML

**Why**: v1.0 HTML output is static. Real landing pages want CTAs, nav sections, scroll anchors — interactive primitives.

**Scope**:
- `render_cta_button(text, href, style)` tool → emits `<a class="...">` in HTML with `role="button"` styling.
- Section anchors (`<section id="..."`) for nav scroll.
- Optional JS component library (ship as a single inlined `<script>` block in the HTML) for reveal-on-scroll, tabs, accordions — non-framework, just vanilla.
- Accessibility baseline: alt text for every image, semantic HTML (`<header> / <main> / <footer>`).

---

## v1.3 — Real PSD type layer

**Why**: Designer UX improvement. v1.0 PSD has named pixel layers (designer can move/resize/reorder but can't double-click to edit text). Real PSD type layers close the gap.

**Scope** (~150 lines, tricky due to psd-tools API brittleness):
- Replace `composite._write_psd` text branch with `TypeLayer` construction (font metadata, actual vector text).
- Fall back to pixel layer if font unavailable on designer's machine.
- Annotate in trajectory: `LayerNode.psd_type_layer: bool`.

**Risk**: psd-tools' type-layer write API is under-documented. Time-box: if not working in 8 hours, leave pixel-layer fallback permanent.

---

## v1.4 — Brand Kit ingestion

**Why**: Claude Design's big UX win is "upload your brand PDF, every artifact respects your palette/typography/imagery." We need parity for teams with existing brand guidelines.

**Scope** (~300 lines):
- `longcat-design brand ingest <path.pdf>` CLI command
- Uses Claude vision to extract: palette (named hex values), typography rules (font roles), logo files, imagery mood.
- Stored in `brands/<name>/brandkit.json`.
- Planner loads `brandkit.json` into DesignSpec when user says "use the Acme brand kit" or `--brand acme`.
- Replaces the v0 `fetch_brand_asset` stub.

---

## v1.5 — Skills (reusable design templates)

**Why**: After successful runs, the (brief → trajectory) pair is a template. Lovart calls these "Skills" and they're a strong retention feature.

**Scope**:
- `longcat-design skill save <session_id> --name "academic-poster-cvpr"` → extract design_spec + key prompts as reusable template.
- `longcat-design skill apply <name> --override "title=新标题"` → instantiate new session from skill with overrides.
- Skills in `skills/<name>.yaml`.

---

## v1.6 — Layout balancer (post-processor)

**Why**: Inspired by Paper2Any's `balancer_agent`. Claude sometimes produces layouts with subtle overlaps or spacing issues. A deterministic post-processor pass catches the obvious ones.

**Scope** (~80 lines): Shapely-based or custom rectangle intersection check on layer bboxes after composite; generates suggestions (or auto-applies within a threshold).

---

## v1.7 — Local model support

**Why**: Full self-hosting story. Some teams can't send API requests to external providers.

**Scope**:
- Support local Ollama / vLLM / llama.cpp endpoint via env var (`LOCAL_LLM_BASE_URL`).
- Adjust planner prompt to work with smaller models (Llama 3.1 70B baseline).
- Fallback model for background generation: local FLUX / SDXL integration (separate deploy, documented).

**Risk**: quality bar; local 70B models don't plan multi-tool workflows as well as Opus 4.7. Document as "experimental" until we get scores within 10% of Opus baseline.

---

## v2.0 — Web UI (optional, distant)

**Why**: Some users won't ever adopt a CLI tool. A thin browser UI over the same core agent could unlock broader adoption.

**Scope**: separate project. Not a v1.x priority. Probably ships as `longcat-design-web` as a separate repo that imports the Python package.

---

## v2.x+ — Future directions

- **Video / animation**: Seedance-style ambient motion videos. Significant.
- **Drawio / diagram output**: steal idea from Paper2Any.
- **Multi-page batch**: regenerate N size variants of same design from one session.
- **Collaboration / sync**: if user demand arises.

---

## Always-on backlog (no version assigned)

Continuous improvements, not feature ships:

- **Cost estimator accuracy**: replace heuristic with real `usage.cost` from OpenRouter response (already returned, just not aggregated).
- **Critic rubric evolution**: audit frequent issue categories, re-balance rubric weights.
- **Prompt cache tuning**: set `cache_control` on planner system prompt + tool catalog (stable turn-to-turn).
- **Font fallback diagnostic**: `longcat-design audit <session>` flags fallback events the planner silently accepted.
- **Showcase gallery**: maintain a `showcase/` directory in repo with ~10 diverse high-quality example artifacts + their session JSONs.

---

## Open questions (not yet roadmap'd)

- **Pricing / support model for OSS**: pure MIT + donation link? Enterprise support tier? Not decided.
- **Brand Kit format**: invent our own schema, or adopt Figma tokens / Adobe XD style format for import compat?
- **How heavily do we market the Longcat-Next tie-in**: enough to signal team credibility, without implying LongcatDesign is just a Longcat-Next sales funnel. Needs a marketing decision.
- **Docs site**: mkdocs on GitHub Pages vs dedicated docs domain vs README-only.

---

## Killed / explicitly out of scope

- **LangGraph / CrewAI integration** — see [DECISIONS.md](DECISIONS.md).
- **Training-data dataset publishing** as primary product — trajectory machinery preserved internally; not the pitch.
- **Canva integration** — they have distribution, not us. We aim open formats (HTML/PPTX).
- **Mobile app** — indefinitely out of scope.
- **Real-time multi-user collaboration** — single-session single-user is the model.
