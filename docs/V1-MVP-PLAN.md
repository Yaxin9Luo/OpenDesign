# v1.0 MVP — Implementation Plan

This doc is **the single-page plan for shipping LongcatDesign v1.0**. For vision see [VISION.md](VISION.md); for version ordering see [ROADMAP.md](ROADMAP.md); for current codebase structure see [ARCHITECTURE.md](ARCHITECTURE.md).

**Target**: an installable, runnable, demonstrable open-source package that conversationally produces 3 artifact types (poster / deck / landing page) with HTML as first-class output.

> **Status (2026-04-19): 8 of 11 items shipped.** Rename ✅, #3 switch_artifact_type ✅, #4 chat shell ✅, #5 edit_layer ✅, #6 HTML renderer + edit toolbar ✅, #6.5 `apply-edits` CLI ✅, **#8 landing schema + mode ✅ — `LayerKind += "section"`, section-tree DesignSpec, flow-layout HTML with auto-themed hero/features/cta/footer, full toolbar + apply-edits round-trip**. Next: #7 PPTX renderer (deck output) — the last artifact type to unlock v1.0 launch.

---

## Scope summary

| Dimension | v0 (now) | v1.0 (target) |
|---|---|---|
| Artifact types | Poster only | Poster + Slide Deck + Landing Page |
| UX | One-shot CLI (`cli.py "<brief>"`) | **Conversational CLI chat shell** (multi-turn REPL) |
| Primary output | PSD + SVG + PNG | **HTML first-class** + PSD + SVG + PPTX + PNG |
| Package name | `design-agent` / `design_agent` | `longcat-design` / `longcat_design` |
| Trajectory emission | User-facing product | Internal session state (preserved, not pitched) |
| KB | Lots of training-data framing | Product framing (open-source Claude Design alt) |

---

## Work breakdown with estimates

Numbered in recommended execution order. Each item is one focused working session.

| # | Item | Size | Status | Files touched | Notes |
|---|---|---|---|---|---|
| 1 | **KB/docs pivot** (this batch + downgrades) | ✅ done | 2026-04-18 | `docs/VISION.md`, `docs/ROADMAP.md`, this file, `docs/DECISIONS.md`, `docs/DATA-CONTRACT.md`, `docs/COMPETITORS.md`, `README.md` | Before touching code — align the narrative first |
| 2 | **Package rename** `design_agent` → `longcat_design` | 30 min | ✅ `ffd4389` | All `longcat_design/*.py` imports, `pyproject.toml`, `prompts/*.md`, smoke, `.claude/settings.local.json` | `git mv` + edit 5 files. Clean `pip install -e .` regenerates the editable link. Uncovered macOS UF_HIDDEN gotcha (see GOTCHAS) |
| 3 | **artifact_type tool + router** | 2 h | ✅ `21dc44f` | `tools/switch_artifact_type.py` new · `tools/__init__.py` register · `prompts/planner.md` update · `schema.py` add `ArtifactType` enum + `DesignSpec.artifact_type` field + `StepType="artifact_switch"` · `planner.py` trace-step wiring · `propose_design_spec.py` fallback | Default to `poster`; registered FIRST in TOOL_SCHEMAS. 7 → 8 tools. |
| 4 | **CLI chat shell (REPL)** | 4 h | ✅ `03517ba` | New `longcat_design/chat.py` (REPL + 8 slash commands) · new `longcat_design/session.py` (`ChatSession`/`ChatMessage`/`TrajectoryRef` schema + save/load/list) · `cli.py` subparsers (`chat` default, `run` one-shot) · `prompts/planner.md` revision-vs-new-artifact guidance · `smoke.py` 6/6 → 7/7 with ChatSession round-trip | Session persistence comes "free" with the chat shell — item #7 below is absorbed here. |
| 5 | **edit_layer tool** | 2 h | ✅ 2026-04-19 | `tools/edit_layer.py` new · `tools/__init__.py` (8→9 tools) · `prompts/planner.md` conversational-edit section · `chat.py` `:edit` stub → NL guidance · `smoke.py` 7/7 → 8/8 | Accepts layer_id + diff (text/font/color/bbox/effects); re-renders just that layer. `:edit` slash is an intentional stub (product-thinking: NL-through-planner > KV-slash; see feedback memory). Dogfood 20260419-133320-46eb3e8a validates planner calls edit_layer organically in critique-revise loops (2×, both ok, incl. partial bbox merge). |
| 6 | **HTML renderer (poster mode) + edit toolbar** | 6 h (est) / ~3 h actual | ✅ 2026-04-19 | New `tools/html_renderer.py` (~400 LOC, poster HTML + inline toolbar JS/CSS) · new `tools/_font_embed.py` (shared WOFF2-subset util for SVG+HTML) · `composite.py` wires HTML output + uses shared font util · `schema.py` adds `CompositionArtifacts.html_path` · `smoke.py` 8/8 with 23-marker HTML assertions | Absolutely-positioned layers with `contenteditable` text. In-browser edit toolbar: drag handle ⤢ + font/size/color/family inputs appear on layer click. Tier 1 Save button → Copy / Download edited HTML. All edits round-tripped via `data-bbox-*` / `data-font-size-px` / `data-fill` / `data-font-family` attrs (authoritative source). Landing mode deferred to #8. |
| 6.5 | **`apply-edits` CLI — round-trip edited HTML → PSD/SVG/PNG** | 2 h / ~1.5 h actual | ✅ 2026-04-19 | New `apply_edits.py` (~250 LOC) · `cli.py` adds `apply-edits` subparser (prints parent run + skipped layers) · `html_renderer.py` emits `<meta name="ld-run-id">` · `pyproject.toml` adds `beautifulsoup4>=4.12` · `smoke.py` 8/8 → 9/9 with seeded-edit round-trip assertion | Parse edited HTML (bs4) → reconstruct `rendered_layers` from data-* attrs + bg `data:` URI (bg PNG decoded directly — original run_dir not required) → re-render text layers via `render_text_layer` → composite → new run_dir with `metadata.parent_run_id` lineage + `metadata.source = "apply-edits"`. Verified with real 春夏之交 poster: title moved + recolored + resized via `sed`-seeded edits on the downloaded HTML → apply-edits produced consistent PSD/SVG/HTML/preview in ~3s, parent correctly identified. |
| 7 | **PPTX renderer (decks)** | 4 h | pending | New `tools/pptx_renderer.py` · deck-specific planner prompt section · `schema.py` adds `DeckStructure` | Based on python-pptx (same lib Paper2Any uses — see [COMPETITORS.md](COMPETITORS.md)). One slide per `LayerNode` with `kind: "slide"`. Text in slides uses native `TextFrame` (PowerPoint-editable). |
| 8 | **Landing-page prompt + schema + renderer** | 2 h / ~2 h actual | ✅ 2026-04-19 | `schema.py` `LayerKind += "section"` + `LayerNode.bbox` optional + `CompositionArtifacts` paths optional · `prompts/planner.md` landing workflow + section-tree DesignSpec example · `tools/html_renderer.py` `write_landing_html()` with flow layout + auto-themed section variants (hero/features/cta/footer) · `tools/composite.py` `artifact_type == landing` route → HTML + simplified preview PNG (no PSD/SVG) · `apply_edits.py` detects `<main class="ld-landing">` and rebuilds the section tree · `session.py`/`chat.py` handle Optional preview/psd/svg paths · `smoke.py` 9/9 → 10/10 with full landing round-trip (3 sections + children preserved, seeded edit `font_size_px 96→128` verified). | Shared edit toolbar (drag handle hidden via CSS for flow layout). Planner skips generate_background / render_text_layer for landing — sections + text nodes go straight from DesignSpec.layer_graph to HTML. |
| 9 | **README product page + quickstart** | 2 h | partial | `README.md` rewrite as product landing | Current README already LongcatDesign-branded; needs screenshots of all 3 artifact types (pending #6/#7) + feature matrix polish + quickstart verify. |
| 10 | **Demo video / screencast** | 2 h | pending | External (asciinema or OBS) | Record 1 session: brief → poster → iterate → switch to deck → iterate → export HTML + PPTX |
| 11 | **Smoke test extension** | 1 h | pending | `longcat_design/smoke.py` — add HTML + PPTX output verification (no API) | Same pattern as existing smoke; generate fake layers, verify each renderer produces a valid file. Current smoke covers PSD + SVG + session round-trip (7/7). |

**Effort done**: ~15.5 h (items 1-6.5 + 8). **Remaining**: ~5 h coding (item 7 PPTX + 11 PPTX smoke) + ~4 h docs/video (items 9, 10).

---

## Detailed dependencies (cross-item)

```
#1 (docs) ──────► (everything else — lock narrative first)
     │
     ▼
#2 (rename) ────► #3, #4, #5 (all touch the package)
     │
     ▼
#3 (artifact_type) ───► #7, #8 (deck + landing need the switching)
     │
     ▼
#4 (chat shell) ───► #5, #9 (edit_layer called from chat; README documents chat)
     │
     ▼
#5 (edit_layer) ──► #4 (chat shell's `:edit` uses this tool)
     │
     ▼
#6 (HTML) ──┐
            ├───► #9 (README shows HTML output)
#7 (PPTX) ──┤
            │
#8 (landing) ► #6 (landing IS HTML) + #3 (routed to)
     │
     ▼
#11 (smoke) ──► #9 (README quickstart runs smoke)
     │
     ▼
#10 (demo) ──► (last; needs everything working)
```

Parallel-safe pairs: #3 & #6 (independent), #7 & #8 (independent once #3 done).

---

## Schema changes (v0 → v1.0)

```python
# NEW enum
class ArtifactType(str, Enum):
    POSTER = "poster"
    DECK = "deck"
    LANDING = "landing"

# DesignSpec gains:
class DesignSpec(BaseModel):
    ...existing...
    artifact_type: ArtifactType = ArtifactType.POSTER   # NEW default

# LayerNode.kind enum broadens:
LayerKind = Literal["background", "text", "brand_asset", "group",
                    "image",     # NEW for v1.1 image insets
                    "slide",     # NEW for decks
                    "section"]   # NEW for landing sections

# CompositionArtifacts adds HTML + PPTX paths:
class CompositionArtifacts(BaseModel):
    psd_path: str | None                 # None for landing (no PSD output)
    svg_path: str | None                 # None for deck/landing
    html_path: str                       # NEW — required for all artifacts
    pptx_path: str | None                # populated for deck
    preview_path: str                    # PNG, flattened preview
    layer_manifest: list[dict]

# NEW — the outer container for a chat session (wraps N trajectories)
class ChatSession(BaseModel):
    session_id: str
    created_at: datetime
    message_history: list[ChatMessage]   # user/assistant alternating
    trajectories: list[Trajectory]       # one per artifact iteration
    current_artifact_type: ArtifactType
    metadata: dict
```

`metadata.version` bumps `v0` → `v1.0`. Loaders branch on it.

---

## Chat shell design (item #4 detail)

Behavior sketch:

```
$ longcat-design                          # default: start new chat session
LongcatDesign v1.0 — type your brief, or :help

> design a 3:4 poster for "国宝回家 公益项目"
 │ [planner thinking... calls propose_design_spec + generate_background + render_text_layer × N + composite]
 │ → Generated poster. Preview: sessions/abc123/artifacts/poster_1/preview.png
 │   PSD: sessions/abc123/artifacts/poster_1/poster.psd
 │   SVG: sessions/abc123/artifacts/poster_1/poster.svg
 │   HTML: sessions/abc123/artifacts/poster_1/poster.html

> make the title bigger and move the 归途 stamp to the top-left
 │ [planner calls edit_layer × 2 + composite]
 │ → Updated. Preview: sessions/abc123/artifacts/poster_1/preview.png

> now do a matching landing page for this project
 │ [planner calls switch_artifact_type(landing) + new propose_design_spec + ...]
 │ → Generated landing page. HTML: sessions/abc123/artifacts/landing_1/index.html

> :export ~/Desktop/guobao
 │ → Copied all artifacts to ~/Desktop/guobao/

> :save
 │ → Session saved: sessions/abc123.json (resume with :load abc123)

> :exit
```

Slash commands:

| Command | Effect |
|---|---|
| `:save [id]` | Persist session state to `sessions/<id>.json` |
| `:load <id>` | Replace current session with loaded one |
| `:new` | Start fresh session (prompts to save current) |
| `:edit <layer_id> <field>=<value>` | Direct layer edit (CLI shortcut; also fires `edit_layer` tool) |
| `:export [path]` | Copy all artifacts + session.json to `path/` |
| `:history` | Show message history (paginated) |
| `:tokens` | Show cumulative tokens + cost |
| `:model <name>` | Switch planner model mid-session |
| `:help` | Command reference |
| `:exit` / `:quit` / Ctrl-D | Exit (prompts save) |

Text not starting with `:` goes to the planner as the next user turn.

---

## HTML renderer design (item #6 detail)

Two modes based on `DesignSpec.artifact_type`:

**Poster mode** — one absolutely-positioned layer per LayerNode:

```html
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>{{title}}</title>
  <style>
    @font-face { font-family: 'NotoSerifSC-Bold';
                 src: url(data:font/woff2;base64,{{subset_b64}}) format('woff2'); }
    .canvas { position: relative; width: {{w}}px; height: {{h}}px; }
    .layer { position: absolute; }
    /* ... minimal Tailwind subset inlined ... */
  </style>
</head>
<body>
  <div class="canvas">
    <img class="layer" src="data:image/png;base64,{{bg_b64}}"
         style="left:0; top:0; width:{{w}}px; height:{{h}}px;">
    <div class="layer"
         style="left:80px; top:110px; width:1200px; height:180px;
                font-family:'NotoSerifSC-Bold'; font-size:220px;
                color:#fafafa; text-align:center;">国宝回家</div>
    <!-- ...more layers... -->
  </div>
</body>
</html>
```

**Landing mode** — semantic sections, flow layout, Tailwind-style classes:

```html
<main class="min-h-screen">
  <header class="flex items-center justify-between p-8">
    <img src="data:image/png;base64,...logo...">
    <nav>...</nav>
  </header>
  <section class="hero bg-gradient-to-br from-stone-900 to-amber-900 ...">
    <h1 class="text-6xl font-serif">国宝回家</h1>
    <p>让流失海外的中华文物，踏上归途</p>
  </section>
  <section class="features grid grid-cols-3 gap-8 ...">...</section>
  <footer>...</footer>
</main>
```

Key: **both modes produce a single self-contained `.html` file** with all CSS inline, all fonts embedded (WOFF2 subset), all images as data URIs. No external deps. Open in browser, works everywhere.

---

## Acceptance criteria for v1.0 launch

Before tagging `v1.0`:

- [ ] `pip install longcat-design` works from TestPyPI
- [ ] `longcat-design --version` returns `1.0.0`
- [ ] `longcat-design` (no args) launches chat shell
- [ ] All 3 artifact types produce viewable output in a real session
- [ ] HTML outputs render correctly in Chrome + Safari + Firefox (spot check)
- [ ] PPTX outputs open in PowerPoint and Keynote (spot check)
- [ ] Smoke test passes (`python -m longcat_design.smoke`)
- [ ] README matches reality (all commands work as shown)
- [ ] Demo video recorded + linked from README
- [ ] Docs pass a "new-to-project in 30 min" onboarding test (Ctrl-A friend tries, reports blockers)
- [ ] Repo is MIT licensed with proper headers, `LICENSE` file, `CONTRIBUTING.md` stub, `CODE_OF_CONDUCT.md`
- [ ] Linked from Longcat team's channels

---

## Risks + mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| HTML renderer complexity explodes (landing page is wide scope) | Medium | Time-box item #6 to 6 h; if landing mode isn't working, ship poster-mode HTML only and defer landing to v1.1 |
| Chat shell state machine gets messy | Medium | Start with the smallest state set (idle / running_tool / waiting_user); add edge cases only when they hit. Don't over-engineer. |
| PPTX Chinese font rendering fails on some systems | Low | Embed common CJK fonts hint in PPTX; document which fonts users should install. Keynote and MS PowerPoint generally have CJK support. |
| Python-pptx type-frame API quirks | Medium | Reference Paper2Any's `Paper2Any/dataflow_agent/toolkits/postertool/src/agents/renderer.py` as a working example. We validated they use native type frames — we can copy that approach. |
| Rename breaks imports in unexpected places | Low | Full `grep -r design_agent` before and after; run smoke test after rename. |

---

## What this plan intentionally omits

- **Marketing / launch strategy** — content, tweets, HN post, timing. Separate plan needed.
- **Docs site hosting** (mkdocs vs GitHub Pages vs custom) — ROADMAP open question.
- **CI/CD** — basic GitHub Actions for lint+test is a 1-hour add before launch, not blocking.
- **Perf benchmarks** — ship v1.0 first, measure in the wild, optimize in v1.x.
