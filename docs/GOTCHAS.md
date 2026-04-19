# Gotchas — runtime quirks we already hit

Each entry: **Symptom** → **Root cause** → **Fix** → optionally **Detection** (how to spot it next time). Add new ones at the top with the date.

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

**Symptom**: After `pip install -e .` on macOS (≥14.x / Sequoia-era), the editable install works via `python -m longcat_design.cli ...` BUT fails via the `longcat-design` console script with `ModuleNotFoundError: No module named 'longcat_design'`. Same Python interpreter, different invocation, different result.

**Root cause**: macOS's file provenance system applies the `UF_HIDDEN` chflag (and a `com.apple.provenance` xattr) to files pip unpacks into `site-packages/`, including the `__editable__.<pkg>-<ver>.pth` file. Python's `site.py` on 3.13+ explicitly skips `.pth` files marked hidden — verbose trace shows:

```
Skipping hidden .pth file: '.venv/lib/python3.14/site-packages/__editable__.longcat_design-0.1.0.pth'
```

Without the `.pth` processed, `site-packages` has no path entry for the editable package. `python -m` still finds the package via CWD being on `sys.path[0]`, but the console script (whose `sys.path[0]` is `.venv/bin/`) has no way to find the package → `ModuleNotFoundError`.

**Fix (short-term, must be re-run after every install)**:

```bash
chflags nohidden .venv/lib/python3.14/site-packages/*.pth
xattr -c .venv/lib/python3.14/site-packages/*.pth   # optional, clears com.apple.provenance
```

Then verify:

```bash
.venv/bin/longcat-design --help   # should show usage, not traceback
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
.venv/bin/python3.14 -v -c "import longcat_design" 2>&1 | grep -i "skipping hidden"
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
