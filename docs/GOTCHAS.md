# Gotchas — runtime quirks we already hit

Each entry: **Symptom** → **Root cause** → **Fix** → optionally **Detection** (how to spot it next time). Add new ones at the top with the date.

---

## 2026-04-21 — pymupdf `find_tables()` false positives are unavoidable; trust the VLM reject step

**Symptom**: `page.find_tables()` reports data tables on pages that contain NO tables (e.g. figure panels with bordered sub-regions; math equation arrays; OCR-example screenshots). On `longcat-next-2026.pdf` it finds 15 candidates across 8 pages, of which only 2 are real data tables.

**Root cause**: pymupdf's table localizer tracks horizontal+vertical rule density and gridded bboxes. Paper figures that happen to have inner borders (e.g. a comparison figure with columns of outputs) score as tables. Math systems like `Ai,0 / Ai,1 / ...` vertical arrays register as 2-col tables.

**Fix (already in v1.2 ingest_document)**:
1. Heuristic filters in `util/pdf.extract_table_candidates`: `min_rows=2, min_cols=2, min_side_pt=60.0`. Cuts 15 → 4 on this paper.
2. `dedup_tables_against_figures`: drop a table candidate when a same-page vector-figure bbox covers ≥ 70 % of the table bbox. Stops figures that look gridded from being processed twice.
3. **VLM reject step is the real filter**: Qwen-VL-Max parses each candidate and returns `is_table: false` for non-tabular content (figure collages, math arrays, OCR examples, decorative bands). Don't try to tune (1) tight enough to match (3) — you'll start dropping real tables.

**Detection**: log line `ingest.pdf.reject_table` with `page` + `reason` shows exactly what the VLM rejected. If you see a real table in `reject_table` logs, the filter is too aggressive.

**Anti-pattern**: bumping `min_rows` / `min_cols` until false positives disappear. On this paper, a 1-row data-recipe table (p.18) is already dropped; going tighter drops more real tables.

---

## 2026-04-20 — Big PDFs via OpenRouter → Anthropic: 20+ min silent hangs or SSL EOF mid-stream

**Symptom**: `ingest_document` on a >15 MB / >30 page PDF via OpenRouter either (a) hangs 20-28 min before timing out with `anthropic.APIConnectionError: Connection error`, or (b) completes after 4-5 min but the connection drops with `httpcore.ConnectError: [SSL: UNEXPECTED_EOF_WHILE_READING]` partway through the response stream.

**Root cause**: OpenRouter proxies the 17 MB base64-encoded `document` content block to Anthropic; the HTTP connection stays open for the full Claude processing time (extended thinking + PDF reading can take 3-10 min on a 43-page paper). Some intermediate hop (Cloudflare / OpenRouter LB / Anthropic frontend) drops long-lived connections. Opus is ~3-5× slower than Sonnet for the same "read this PDF" task, compounding the timeout risk.

**Fix** (partially mitigated):
- `ingest_document` now uses **Sonnet 4.6 by default** via `settings.ingest_model`, cutting wall time from ~28 min (Opus) to ~5 min (Sonnet). Override via `INGEST_MODEL=anthropic/claude-opus-4.7` for complex visual papers.
- Explicit 10-min HTTP timeout on the ingest client (`settings.ingest_http_timeout`) — fails fast with a clear error instead of silent 20-min hang. Override via `INGEST_HTTP_TIMEOUT=<seconds>`.
- `max_retries=1` on the ingest client so a transient SSL blip doesn't trigger multiple $2 retries.

**Full fix pending v1.1.5**: streaming ingest response parsing (tolerates mid-stream blips), auto-chunking for >50-page PDFs.

**Detection**:
- stderr shows `ingest.pdf.structure.request` with no matching `ingest.pdf.structure.response` for 5+ minutes.
- Traceback ends in `anthropic.APIConnectionError` or `httpcore.ConnectError [SSL: UNEXPECTED_EOF_WHILE_READING]`.
- Retrying the same run on the same paper often works on the second attempt.

**Workaround if persistent**: `export ANTHROPIC_API_KEY=<stock key>` + unset `OPENROUTER_API_KEY` to bypass OpenRouter's intermediary entirely. Or split the PDF via `pdftk paper.pdf cat 1-30 output first-half.pdf` and ingest halves separately.

---

## 2026-04-20 — Anthropic returns `{"figures": null}` instead of `[]` for figure-light papers → `list(None)` crash

**Symptom**: `ingest_document` returns `tool.exception: 'NoneType' object is not iterable` AFTER a successful `ingest.pdf.structure.response` log (indicating the Anthropic call completed). The 258-second Sonnet call wasn't wasted — the crash happens in manifest post-processing.

**Root cause**: Claude can emit JSON like `{"figures": null, "sections": [...]}` when a paper has no visual figures (rare for academic papers, more common for marketing one-pagers). `manifest.setdefault("figures", [])` is a **no-op when the key is already present with a `None` value** — it doesn't replace None with the default. Downstream `list(manifest["figures"])` then hits `TypeError: 'NoneType' object is not iterable`.

**Fix** (v1.1 `dc93960`): replaced `setdefault` with an explicit coercion loop:

```python
for list_key in ("sections", "figures", "tables", "authors", "key_quotes"):
    if not isinstance(manifest.get(list_key), list):
        manifest[list_key] = []
```

**Detection**: `tool.exception` with `'NoneType' object is not iterable` or `'NoneType' object is not subscriptable` logged *after* a successful `ingest.pdf.structure.response`.

---

## 2026-04-20 — Ingested image layers have `bbox: None` → poster composite `NoneType is not subscriptable` in `_write_psd`

**Symptom**: `ingest_document` succeeds, `propose_design_spec` succeeds, `composite` fails with `PSD write failed: 'NoneType' object is not subscriptable`. Planner keeps retrying `propose_design_spec → composite` and loops until 30-turn cap.

**Root cause**: `ingest_document` registers figures in `ctx.state["rendered_layers"]` with `bbox: None` because ingestion has no intrinsic placement — the planner decides where to put each figure on the poster canvas via `spec.layer_graph`. But the poster composite path reads from `rendered_layers` (not `spec.layer_graph`) and does `bbox["x"]` on every layer.

**Fix** (v1.1 `dc93960`): new `_hydrate_poster_layer_bboxes(rendered, spec)` helper in `composite.py` — companion to `_hydrate_landing_image_srcs` / `_hydrate_deck_image_srcs`. Copies bbox from `spec.layer_graph` onto `rendered_layers` records that lack one, matching by `layer_id`. Called in the poster composite path before `sorted_layers` is built.

**Coupled poster fix**: `_write_psd`, `_write_svg`, `_write_preview`, and `html_renderer.write_html` previously handled only `kind in (background, text, brand_asset)` — `kind == "image"` was silently skipped with a comment. New branches resize the native-sized ingested PNG to bbox dimensions and place at `(bbox.x, bbox.y)`. Poster SVG now emits `<image>` with base64 href; poster HTML emits `<div class="layer image">` + `<img>`.

**Detection**: `composite` returns `PSD write failed: 'NoneType' object is not subscriptable` AND the planner's preceding `propose_design_spec` included `kind: "image"` children with `layer_id` starting with `ingest_fig_` or `ingest_img_`.

---

## 2026-04-20 — Planner `max_tokens=4096` silently truncates large DesignSpec JSON → 30-turn spin without finalize

**Symptom**: Dogfooding a 10-slide deck (or 30+ layer poster, long landing) causes the runner to exit with `RuntimeError: planner exited without proposing a DesignSpec` after exactly `max_planner_turns` (30) planner.turn events. Logs show `propose_design_spec` never appearing in `tool.call` events even though the planner clearly intends to call it. Same brief at smaller complexity (3 slides) works instantly.

**Root cause**: `open_design/planner.py` previously capped `client.messages.create(..., max_tokens=4096, ...)`. A full deck DesignSpec with 10 slides × ~5 text/image children × bbox+text+font metadata easily exceeds 4096 output tokens. Anthropic truncates the assistant output at the cap; the `tool_use` block is emitted without its argument JSON being complete; the SDK silently produces an assistant message with no `tool_use` items. The planner then sees `stop_reason != "end_turn"` + empty tool_uses → does NOT break → loops back into the next turn with the (still-empty) tool-result message history → repeats until `max_planner_turns` → runner errors.

**Fix**: Bumped `max_tokens` to `16384` (Opus supports up to 32K output tokens). Also added `log("tool.call", tool=...)` + `log("tool.result", tool=..., status=..., summary=...)` events inside `_invoke` so future debugging doesn't require trajectory introspection.

**Detection**:
- stderr shows `planner.turn` climbing to 30 without any matching `tool.call` / `tool.result` log lines.
- Runner raises `"planner exited without proposing a DesignSpec"`.
- Wall time spent ≈ 30× per-turn API round-trip (~7 min for an Opus-driven 30-turn spin).

**Related**: If you hit this in the future (e.g. a 30-slide academic deck or a huge landing), bump `max_tokens` further — Opus supports 32K output tokens, `max_tokens=32000` is safe.

---

## 2026-04-20 — PPTX delegates CJK font rendering to the consuming app (by design)

**Symptom**: User opens a `deck.pptx` in PowerPoint on a fresh Windows machine and Chinese titles render as boxes (tofu).

**Root cause**: By design, `pptx_renderer.py` does NOT embed fonts into the `.pptx` — it writes `run.font.name = "NotoSerifSC-Bold"` and delegates rendering to the consumer's font engine. This mirrors [Paper2Any's approach](COMPETITORS.md) and avoids two problems: (a) embedding ~40 MB of Noto SC into every deck, (b) font-licensing review (OFL is redistributable but the in-pptx embedding semantics differ from bundled OTF). Consumer apps (PowerPoint, Keynote, Google Slides) all ship system CJK fonts on modern macOS / Windows; on Linux a `sudo apt install fonts-noto-cjk` is typically required.

**Fix (user side)**: Install Noto Sans SC + Noto Serif SC (OFL, free) from Google Fonts. On macOS + Windows 10+ the system ships PingFang / Microsoft YaHei which PowerPoint maps to `NotoSansSC-Bold` via font-fallback rules automatically.

**Fix (future v1.x)**: Add an `EMBED_FONTS=1` env var that turns on font embedding (requires `python-pptx` packaging internals + OFL license review). Tracked in ROADMAP.md v1.x.

**Detection**: User reports "all Chinese shows as boxes." First-line check: ask what OS + whether Noto SC is installed. Reproduction on your machine is unlikely if you dev on macOS with system CJK fonts installed.

---

## 2026-04-19 — Landing critic judges Pillow preview.png, not the real HTML → false "fail" verdicts

**Symptom**: Good landing HTML gets scored `fail / 0.18 / 8 blocker issues` by critic. Issues list looks like "dark empty canvas, tofu emojis, cards overlap, hero 60px not 180px" — all things the real browser-rendered HTML doesn't have. Real instance: first milk-tea landing dogfood `20260419-192002-bfcf00b0` produced a polished claymorphism landing with 180px hero + correct emojis + section layout, but critic called it unshippable.

**Root cause**: Landing mode's `preview.png` is a simplified Pillow wireframe (composite.py `_write_landing_preview`) — it stacks section headlines on a color band but can't actually render CSS, so things like grid layouts, backdrop-filter, and system-font emoji coverage are all missing. The critic was handed this bad raster and graded it honestly against the poster-visual rubric.

**Fix**: `critic.py` now branches on `design_spec.artifact_type`. For LANDING it uses `prompts/critic-landing.md` (content-level rubric against the section tree JSON) with no image attachment. Runner.py + schema extended so `IssueCategory` accepts `"copy"` / `"content"` which the text-only critic naturally uses. Same milk-tea brief re-run after the fix (`20260419-204503-b5300878`): critic pass 0.94, 2 minor issues.

**Rule of thumb**: If the artifact is HTML-primary (landing), the DesignSpec + section tree IS the source of truth. Grading a lossy rasterization of a good HTML is strictly worse than grading the DesignSpec directly. For visual-primary artifacts (poster, eventually deck PPTX screenshots), vision critique stays — the rendered artifact IS what the user consumes.

**Detection**: If a landing critique comes back with blockers of the form "canvas is dark/empty," "emojis are tofu," "text overlaps," "grid is broken" while the real HTML opens fine in your browser → you're on the old vision-based critic path. Check that `critic.py` is routing LANDING to `_evaluate_landing` and that `prompts/critic-landing.md` exists in the prompts dir.

---

## 2026-04-19 — Landing image layers declared in DesignSpec but `src_path=None` until composite

**Symptom**: Planner calls `propose_design_spec` with `children: [{kind: "image", layer_id: "H0_img", ...}]`, then calls `generate_image(layer_id="H0_img", ...)` — both succeed, but the rendered HTML has no `<img>` for that layer, just empty sections. Grep the output: zero `data:image/png;base64,` data URIs.

**Root cause**: `generate_image` writes the PNG + metadata into `ctx.state["rendered_layers"]["H0_img"]` with a real `src_path`, but the section tree lives on `ctx.state["design_spec"].layer_graph` — the image layer in `section.children` was declared in `propose_design_spec` with `src_path=None` (the planner can't know the path in advance). The HTML renderer walks the section tree for output, never `rendered_layers`, so it sees `src_path=None` and silently skips.

**Fix**: `tools/composite.py` `_hydrate_landing_image_srcs` walks the section tree before render and copies `src_path` (and `aspect_ratio`) from matching entries in `rendered_layers` onto the children using pydantic `model_copy(update=...)`. Called at the top of `_composite_landing` before manifest-build and `write_landing_html`. Once hydrated, the renderer inlines each image as a `data:` URI via `_inline_image`.

**Rule of thumb for two-step planner flows**: When a tool writes content into `rendered_layers` but the DesignSpec also needs to carry that content for downstream consumers (trajectory persistence, round-trip parsers, downstream renderers), build a hydration helper that bridges them. Don't ask the planner to call `propose_design_spec` twice — the bookkeeping should be runtime, not LLM work.

**Detection**: If `composite.landing.done` logs `images=0` but `rendered_layers` contains kind="image" entries → hydration didn't run or didn't find matches. Check that every image child's `layer_id` is identical to the one passed to `generate_image`.

---

## 2026-04-19 — Chat "revise" turns regress to the few-shot anchor (wrong poster generated)

**Symptom**: First turn of a chat session generates poster A (e.g. a Neural Networks course poster, 9 layers). Second turn user says "make the title bigger". Agent runs, produces a 4-layer poster about 国宝回家 with palette `['#1a0f0a','#fafafa','#a02018','#c9a45a']` and layers `国宝回家 / National Treasures / 归途` — nothing to do with the NN poster. Real instance: session `session_20260418-231218_f285acbc`, turn-2 run_id `20260418-232431-0ce827fc`, $1.44 wasted.

**Root cause**: Initial v1.0 #4 context injection was metadata-only: the contextual brief told the planner "there's a prior artifact at `trajectory: /Users/.../out/trajectories/<run_id>.json`" but the planner has NO filesystem read tool in its action space — it can't actually load that file. When asked to revise an artifact it can't see, and instructed to call `propose_design_spec`, the planner pattern-matches to the most detailed concrete example in its context: the 国宝回家 few-shot anchor in `prompts/planner.md`. It copies that example's palette/mood/layers verbatim and tags the brief as "国宝回家... revision: make the title bigger." See DECISIONS.md 2026-04-19 entry for the full post-mortem.

**Fix**: `chat.py._build_contextual_brief` now loads the prior trajectory's `design_spec` from disk and dumps the full JSON (palette, mood, canvas, layer_graph with per-layer text/font/size/bbox) into the contextual brief inside a ```` ```json ```` block. `prompts/planner.md` got a guard clause: "when the brief prefix contains a `### Prior DesignSpec` block, COPY it verbatim; few-shot anchors are for FIRST-TURN briefs only."

**Detection**: If a chat-revision turn produces:
- an output whose palette/mood/layer_graph matches the few-shot anchor (国宝回家-flavored) but the user's turn-1 brief was about something else
- `design_spec.brief` contains the string "国宝回家" when the session's first message never mentioned it
- n_layers drops dramatically from turn 1 → turn 2 (agent rebuilding from scratch vs tweaking)

→ this regression is back. Check that `_build_contextual_brief` is actually loading the prior spec (JSON block in trajectory.brief) and that `prompts/planner.md` still has the guard clause.

**Rule of thumb for future context-injection tuning**: ALWAYS dump the actual prior state (spec, layer_graph, critique issues). Metadata pointers are useless to a planner with no file-read tool. Token cost is tiny (~1K tokens per 10-layer spec ≈ $0.02) compared to the cost of wasted generation (~$1.5 per wrong artifact).

---

## 2026-04-18 — macOS marks pip-installed `.pth` files as UF_HIDDEN, Python silently skips them → `ModuleNotFoundError` via script entry

**Symptom**: After `pip install -e .` on macOS (≥14.x / Sequoia-era), the editable install works via `python -m open_design.cli ...` BUT fails via the `open-design` console script with `ModuleNotFoundError: No module named 'open_design'`. Same Python interpreter, different invocation, different result.

**Root cause**: macOS's file provenance system applies the `UF_HIDDEN` chflag (and a `com.apple.provenance` xattr) to files pip unpacks into `site-packages/`, including the `__editable__.<pkg>-<ver>.pth` file. Python's `site.py` on 3.13+ explicitly skips `.pth` files marked hidden — verbose trace shows:

```
Skipping hidden .pth file: '.venv/lib/python3.14/site-packages/__editable__.open_design-0.1.0.pth'
```

Without the `.pth` processed, `site-packages` has no path entry for the editable package. `python -m` still finds the package via CWD being on `sys.path[0]`, but the console script (whose `sys.path[0]` is `.venv/bin/`) has no way to find the package → `ModuleNotFoundError`.

**Fix (short-term, must be re-run after every install)**:

```bash
chflags nohidden .venv/lib/python3.14/site-packages/*.pth
xattr -c .venv/lib/python3.14/site-packages/*.pth   # optional, clears com.apple.provenance
```

Then verify:

```bash
.venv/bin/open-design --help   # should show usage, not traceback
```

**Fix (long-term, persistent)**: wrap `pip install` in a shell alias or makefile target that always runs `chflags nohidden` afterward. Example:

```bash
# in your shell rc
alias pip-editable='pip install -e . && chflags nohidden .venv/lib/python3.*/site-packages/*.pth 2>/dev/null; true'
```

Or in a project `Makefile`:

```makefile
install:
	.venv/bin/pip install -e .
	@chflags nohidden .venv/lib/python3.*/site-packages/*.pth 2>/dev/null; true
	@xattr -c .venv/lib/python3.*/site-packages/*.pth 2>/dev/null; true
```

**Notes**:
- Flag comes BACK after Python reads the file — macOS re-applies `UF_HIDDEN` on access in some circumstances. Running chflags immediately before script invocation is the reliable pattern if you hit transient failures.
- Also seen with `uv` installs (same root cause). Same fix.
- Linux: not affected (no UF_HIDDEN concept).

**Detection**: The smoking gun is the verbose import trace:

```bash
.venv/bin/python3.14 -v -c "import open_design" 2>&1 | grep -i "skipping hidden"
```

If you see `Skipping hidden .pth file`, apply the fix.

---

## 2026-04-18 — Figma SVG import: text breaks, fonts substitute, layout explodes

**Symptom**: Open `poster.svg` in Figma. Text gigantic, characters wrap mid-word ("NATIO / NAL / TREASU / RES"), `归途` stamp floats off canvas, generally looks unrelated to the browser-rendered version.

**Root cause**: Figma's SVG importer:
1. Drops the embedded `@font-face` data URI fonts → falls back to a system font with very different metrics → text effectively rendered ~2x larger than declared.
2. Mishandles `text-anchor="middle"` on `<text>` without an explicit `width` attribute → treats x as text *start* not anchor.
3. Possibly auto-wraps `<text>` content based on guessed widths.

**Fix (workarounds, the SVG itself is W3C-correct)**:
- **Use the browser** as the SVG renderer for previewing — it's correct.
- For SVG editing, use **Inkscape, Illustrator, or Affinity Designer** — all respect embedded fonts and `text-anchor`.
- For Figma workflow specifically: open SVG in browser → print to PDF → import PDF in Figma (text becomes paths, not editable, but layout intact).
- OR import the PSD into Figma (named pixel layers preserved) and add Figma-native text overlays.

**Long-term fix**: v0.9 Figma plugin (see [ROADMAP.md](ROADMAP.md)) reads `trajectory.json` and constructs native Figma text + image nodes — bypasses SVG entirely.

**Detection**: If a designer reports "your SVG is broken in my tool," ask which tool. If Figma → known. If Inkscape/Illustrator → real bug, escalate.

---

## 2026-04-18 — SVG character set drift after manual text edits

**Symptom**: Designer edits SVG `<text>国宝回家</text>` → `<text>国宝回家了</text>`. The `了` renders as `□` or a fallback font in the browser.

**Root cause**: We subset the embedded WOFF2 font to only the glyphs in the *original* text (saves ~25 MB per font; see [DECISIONS.md](DECISIONS.md) "SVG fonts: subset to used glyphs"). New characters added post-export aren't in the subset.

**Fix**:
- Restrict text edits to characters present in the original.
- OR run the v0.2 rerender command (when shipped) — it re-subsets to the new character set.
- OR manually re-subset by running `tools/composite._build_font_face_css` against the new text.

**Detection**: If a layer shows `□` glyphs after an edit, this is almost certainly it.

---

## 2026-04-18 — OpenRouter Anthropic-format `base_url` must omit `/v1`

**Symptom**: Calling Anthropic SDK with `base_url="https://openrouter.ai/api/v1"` → 404 returning OpenRouter's HTML error page (HTML in the exception message — looks scary, just means wrong URL).

**Root cause**: Anthropic SDK appends `/v1/messages` itself. So `base_url + "/v1/messages"` = `https://openrouter.ai/api/v1/v1/messages` (double `/v1/`) → 404.

**Fix**: Use `base_url="https://openrouter.ai/api"`. SDK appends `/v1/messages` → final URL `https://openrouter.ai/api/v1/messages` ✅.

Already enforced in [`config.py:OPENROUTER_BASE_URL`](../design_agent/config.py).

**Detection**: 404 with a giant HTML response body in the Python traceback = wrong URL shape.

---

## 2026-04-18 — Stray shell `ANTHROPIC_BASE_URL` masks OpenRouter mode

**Symptom**: Even after setting `OPENROUTER_API_KEY` in `.env`, requests go to `https://api.anthropic.com` and 401 with `invalid x-api-key` (because the OpenRouter key isn't valid against Anthropic's stock endpoint).

**Root cause**: The user's shell environment had `ANTHROPIC_BASE_URL=https://api.anthropic.com` exported (from `.zshrc` or similar). Original config.py respected it, overriding the OpenRouter URL.

**Fix**: In OpenRouter mode, force `base_url` to OpenRouter's URL regardless of shell env. Already enforced in [`config.py:load_settings()`](../design_agent/config.py) — see the `if or_key` branch.

**Detection**: Print `settings.anthropic_base_url` early; if it's `https://api.anthropic.com` while `OPENROUTER_API_KEY` is set, that's wrong.

---

## 2026-04-17 — `.env` not loaded because shell exports an empty value

**Symptom**: `RuntimeError: ANTHROPIC_API_KEY missing` even though `.env` clearly has the key.

**Root cause**: `python-dotenv`'s default behavior is to NOT override env vars that already exist in `os.environ`. If shell startup exports `ANTHROPIC_API_KEY=` (empty string) — which keychain integrations and some `.zshrc` patterns do — `os.environ["ANTHROPIC_API_KEY"]` is `""` (empty but present) → dotenv skips the .env value → app sees empty.

**Fix**: `load_dotenv(REPO_ROOT / ".env", override=True)`. Already enforced in [`config.py`](../design_agent/config.py).

**Detection**: Diagnostic snippet:

```python
from dotenv import load_dotenv
import os
print("Pre-dotenv has key:", "ANTHROPIC_API_KEY" in os.environ,
      "len:", len(os.environ.get("ANTHROPIC_API_KEY", "")))
load_dotenv(".env", override=True)
print("Post-dotenv len:", len(os.environ.get("ANTHROPIC_API_KEY", "")))
```

If pre-dotenv shows `True, len: 0` and post-dotenv shows the right length, that's exactly this.

---

## 2026-04-17 — Gemini SDK saves JPEG bytes regardless of file extension

**Symptom**: `part.as_image().save("foo.png")` → file extension is `.png` but `file foo.png` reports `JPEG image data`. Downstream tools embedding the PNG (psd-tools, browsers parsing SVG `<image>`) get confused.

**Root cause**: `genai.types.Image.save()` writes the raw `inline_data.data` bytes verbatim. Gemini's image generation returns JPEG bytes inline, not PNG, regardless of file extension you ask for.

**Fix**: Always re-encode through Pillow:

```python
from io import BytesIO
from PIL import Image as PILImage
pil = PILImage.open(BytesIO(part.inline_data.data))
if pil.mode != "RGB":
    pil = pil.convert("RGB")
pil.save(out_path, format="PNG", optimize=True)
```

Already enforced in [`tools/generate_background.py`](../design_agent/tools/generate_background.py).

**Detection**: `file out/runs/.../layers/bg_*.png` should report `PNG image data`. If it says `JPEG`, the re-encode regressed.

---

## 2026-04-17 — `psd-tools` 1.11+: Group has no `create_pixel_layer` method

**Symptom**: `AttributeError: 'Group' object has no attribute 'create_pixel_layer'`.

**Root cause**: The pattern from older psd-tools tutorials of `group.create_pixel_layer(...)` doesn't exist on Group. The factory method only lives on `PSDImage`.

**Fix**: Two-step — create the layer on the PSDImage, then move it into the group:

```python
text_group = psd.create_group(name="text", open_folder=True)
layer = psd.create_pixel_layer(crop, name=L["name"], top=by, left=bx, ...)
text_group.append(layer)
```

Already enforced in [`tools/composite.py:_write_psd`](../design_agent/tools/composite.py).

**Detection**: Smoke test step 5 catches this.

---

## 2026-04-17 — Anthropic vision input: 5 MB / 8000×8000 cap

**Symptom**: Critic call fails with input-size error if you send the full 2K poster preview as-is.

**Root cause**: Anthropic vision input has hard limits — image must be ≤5 MB and ≤8000×8000.

**Fix**: Downscale preview to ≤1024 px long edge before sending. Encode as JPEG (smaller than PNG for photographic posters). Already enforced in [`critic._downscale_b64`](../design_agent/critic.py); `Settings.critic_preview_max_edge = 1024`.

The original full-resolution preview is preserved on disk; only the version sent to the critic is downscaled.

**Detection**: If critic suddenly starts failing on larger posters, check the downscale step.

---

## 2026-04-17 — Anthropic credit balance can run out mid-pipeline

**Symptom**: `BadRequestError: 400 - 'Your credit balance is too low to access the Anthropic API.'` during a planner turn.

**Root cause**: Stock Anthropic accounts run on prepaid credits; if the balance dips below the per-call estimate, the API rejects.

**Fix**:
- Switch to OpenRouter (`OPENROUTER_API_KEY`) — pay-as-you-go, fewer hard cutoffs, single key for many providers.
- Or top up at https://console.anthropic.com/settings/billing.

**Detection**: 400 with this exact message text. Different from auth errors (which are 401).

---

## 2026-04-17 — Pillow `ImageFont.truetype` requires path to be string, not Path

**Symptom**: Less of a gotcha and more a thing to remember. Older Pillow versions raise on `Path` objects for `font` argument.

**Fix**: Always wrap in `str(path)` when passing fonts to Pillow APIs. Already enforced in [`render_text_layer.py`](../design_agent/tools/render_text_layer.py).

---

## Generic gotcha: cost estimator is a heuristic

**Symptom**: `metadata.estimated_cost_usd` can disagree with what you actually pay (especially via OpenRouter, which has its own per-call pricing).

**Root cause**: [`runner._estimate_cost`](../design_agent/runner.py) uses Anthropic stock pricing as a worst-case heuristic. OpenRouter's response includes a real `usage.cost` field per call which we currently don't aggregate.

**Fix scheduled**: v0.x always-on backlog item (see [ROADMAP.md](ROADMAP.md)) — sum the `usage.cost` from each `messages.create` response.

**For now**: trust OpenRouter's dashboard for actual spend; treat trajectory's `estimated_cost_usd` as an upper bound.

---

## Recurring meta-gotcha: KB drift

If you find that something in the docs disagrees with reality, **the code is right and the doc is the bug**. Fix the doc immediately, don't just note it. KB drift is the worst possible failure mode of a knowledge base.
