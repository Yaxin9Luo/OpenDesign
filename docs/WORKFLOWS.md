# Workflows

Day-to-day recipes. For *why* the system is shaped the way it is, see [VISION.md](VISION.md) and [DECISIONS.md](DECISIONS.md). For module reference, see [ARCHITECTURE.md](ARCHITECTURE.md).

---

## Setup (one-time)

Environment is managed by [uv](https://docs.astral.sh/uv/). Install uv once, then:

```bash
cd /Users/yaxinluo/Desktop/Projects/Design-Agent
uv sync                    # reads pyproject.toml + uv.lock → .venv with pinned deps
cp .env.example .env
# edit .env to fill in:
#   GEMINI_API_KEY (required)
#   AND one of:
#     OPENROUTER_API_KEY  (preferred — pay-as-you-go, single key, both Claude + cost reporting)
#     ANTHROPIC_API_KEY   (stock — needs balance topped up)
```

Day-to-day commands are prefixed with `uv run` (auto-syncs deps and uses the project venv). You can still `source .venv/bin/activate` if you prefer the old workflow — uv just manages the venv, it does not replace it.

If both LLM keys are set, **OpenRouter wins**. To force stock Anthropic, comment out `OPENROUTER_API_KEY`. See [GOTCHAS.md](GOTCHAS.md) if env vars don't load (shell-exported empty values can mask `.env`).

---

## Smoke test (no API, no $$, ~5 sec)

Use this whenever you change tools, schema, fonts, or composite logic:

```bash
uv run python -m longcat_design.smoke
```

Verifies 7 steps: imports (incl. chat + session modules), tool registry shape (8 tools + `switch_artifact_type` first), `Trajectory` Pydantic round-trip, font loading, real composite call against a stub background, SVG vector text + embedded fonts, **ChatSession save/load round-trip**.

Outputs go to `out/smoke/`. Inspect `poster.psd` (should have 3 named layers: `background` + group `text` containing `title` + `subtitle`) and `poster.svg` (`<text>国宝回家</text>` should be a real vector element).

---

## Two ways to run: chat (default) vs one-shot

### Chat shell (v1.0 default — conversational multi-turn)

```bash
uv run python -m longcat_design.cli
# or equivalently:
uv run python -m longcat_design.cli chat
```

Launches a REPL. Type a brief, agent generates, iterate. Every turn auto-saves to `sessions/<session_id>.json`. See [§slash commands](#slash-commands) below.

Resume a prior session:

```bash
uv run python -m longcat_design.cli chat --resume session_20260418-214348_ea6c0de9
```

### One-shot (legacy, for scripting / CI / single-brief work)

```bash
uv run python -m longcat_design.cli run "<your brief>"
```

Examples:

```bash
# Minimal — replicates the 国宝回家 reference case
uv run python -m longcat_design.cli run "国宝回家 公益项目主视觉海报，竖版 3:4"

# Academic poster (text-heavy)
uv run python -m longcat_design.cli run "学术海报：CVPR 2026 投稿《<title>》。需要：主标题 + 5 位作者及 affiliation + 4 个 section（Abstract / Method / Results / Conclusion）+ 底部 conference info + 右上角 QR 占位框。竖版 3:4。"
```

Both modes produce artifacts in `out/runs/<run_id>/` (PSD/SVG/preview/layers) and trajectories in `out/trajectories/<run_id>.json`. Chat mode ADDITIONALLY wraps trajectories under `sessions/<session_id>.json`.

**Cost & time** per artifact (rough, see [DATA-CONTRACT.md](DATA-CONTRACT.md) and [ARCHITECTURE.md](ARCHITECTURE.md#performance-baselines-reference) for measured baselines):

- 5-layer simple poster, single-pass critique: ~100 s, ~$1.4
- 18-layer text-heavy poster, single-pass critique: ~200 s, ~$2.5
- 5-layer complex brief, 2-iter critique: ~300 s, ~$3.7

---

## Slash commands

Inside the chat REPL, any line starting with `:` is a slash command; everything else is a brief for the planner.

| Command | Effect |
|---|---|
| `:help` · `:h` · `:?` | Show command reference |
| `:save [id]` | Persist session to `sessions/<id>.json` (default: current session_id; auto-saves happen after every turn) |
| `:load <id>` | Replace current session with the loaded one (auto-saves current first) |
| `:new` | Start fresh session (auto-saves current). New session_id generated. |
| `:list` · `:ls` | List recent sessions (most recent first, current marked with `*`) |
| `:history` | Show message history with timestamps + trajectory refs |
| `:tokens` · `:cost` | Cumulative session stats per artifact |
| `:export [path]` | Copy all artifacts + session.json to `path/` (default: `~/Desktop/<session_id>/`) |
| `:exit` · `:quit` · `:q` · Ctrl-D | Exit (auto-saves session) |

**Not yet shipped** (blocked on v1.0 #5 `edit_layer` tool):

- `:edit <layer_id> <field>=<value>` — currently, revisions flow through full `propose_design_spec` re-call. Once #5 ships, this slash will invoke `edit_layer` directly for cheaper single-layer tweaks.

## Chat mode — revision vs new-artifact

When a session already has prior artifacts, each new brief is automatically prefixed with a summary of the latest trajectory before going to the planner. The planner then decides:

- **Revision** (keep same artifact, tweak): user says "make the title bigger", "change palette to red", "try a cleaner composition" → planner re-calls `propose_design_spec` with adjustments, re-renders affected text layers, recomposites. Background stays unless explicitly asked.
- **New artifact** (possibly different type): user says "now a matching landing page", "give me a horizontal version", "a poster for DIFFERENT project X" → planner calls `switch_artifact_type` first, then fresh `propose_design_spec`. Prior artifact preserved in session for reference.

The decision rules live in [`prompts/planner.md`](../prompts/planner.md) "Chat mode: revision vs new-artifact decision" section.

## Inspect outputs

### Find latest run id (shell helper)

```bash
# Most recent run
LATEST_RUN=$(ls -t out/runs/ | head -1)

# Most recent session
LATEST_SESSION=$(ls -t sessions/ | head -1 | sed 's/\.json$//')
```

### View the flat preview

```bash
open out/runs/$LATEST_RUN/preview.png
```

### Open SVG in browser (THE BEST way to see the truth)

```bash
open out/runs/$LATEST_RUN/poster.svg
```

The browser is the reference renderer for our SVG output. Every `<text>` element renders correctly with the embedded WOFF2 font.

### Inspect PSD layer tree

```bash
uv run python -c "
from psd_tools import PSDImage
p = PSDImage.open('out/runs/<run_id>/poster.psd')
def walk(n, d=0):
    for L in n:
        kind = 'group' if L.is_group() else 'pixel'
        print(f'  {\"  \"*d}- [{kind}] {L.name!r}  bbox={L.bbox}')
        if L.is_group(): walk(L, d+1)
walk(p)
"
```

### Verify SVG text is vector (not rasterized)

```bash
grep -oE '<text[^>]*>[^<]+</text>' out/runs/<run_id>/poster.svg
```

Should return one line per text layer.

### Read the trajectory

```bash
uv run python -c "
import json
t = json.load(open('out/trajectories/<run_id>.json'))
print(f'brief: {t[\"brief\"]}')
print(f'layers: {len(t[\"layer_graph\"])}')
print(f'trace steps: {len(t[\"agent_trace\"])}')
print(f'critiques: {len(t[\"critique_loop\"])}')
print(f'metadata: {t[\"metadata\"]}')
"
```

---

## Designer-edits workflow (v0)

> **Honest framing**: in v0, the SVG is the truly editable artifact. The PSD has the right layer structure (named, positioned) but text layers are RASTER, not type layers. See [ROADMAP.md](ROADMAP.md) v0.2 for real PSD type layer.

### Path A — quick text tweak (any editor)

Open `poster.svg` in any text editor (VSCode, Sublime), find:

```xml
<text fill="#fafafa" font-family="'NotoSerifSC-Bold'"
      font-size="240" text-anchor="middle"
      x="768" y="420">国宝回家</text>
```

Change content/color/size/position attrs, save, reload in browser. Done.

**Caveat — character set drift**: the embedded font is subsetted to only the glyphs in the original text. If you change `国宝回家` → `国宝回家了`, the `了` won't have a glyph and renders as a fallback (or `□`). To add new characters cleanly, **rerender** (see Path D, or v0.1 rerender command).

### Path B — vector editor (Inkscape, Illustrator, Affinity Designer)

```bash
open -a Inkscape out/runs/<run_id>/poster.svg
```

These respect embedded WOFF2 fonts and render text as editable vector objects. Inkscape is free; Illustrator and Affinity Designer are paid.

### Path C — Photoshop (PSD)

Open `poster.psd`. You'll see ≥ 5 named layers (background + a `text` group containing per-element pixel layers). What you can do:

- ✅ Move / resize / rotate / re-order layers
- ✅ Adjust opacity / blend mode
- ✅ Hide a layer; add your own type layer over it
- ❌ Double-click to edit text content (text layers are RASTER, not type)

For text edits in PS, the practical workflow is: hide the existing pixel layer, add a fresh PS Type layer over it. Or use Path A/B for the edit, then re-export.

### Path D — Figma (currently broken)

Figma's SVG importer mishandles `text-anchor`, drops embedded `@font-face`, and breaks layout. **Don't use Figma for SVG editing.** See [GOTCHAS.md](GOTCHAS.md) entry "Figma SVG import" for the full diagnosis and workarounds.

If you must use Figma:

1. Open SVG in browser → print to PDF → import PDF in Figma. Layout intact, but text becomes paths (not editable). Use as a layout reference, then add Figma-native text layers on top.
2. Or import the PSD directly into Figma — keeps the layer structure (named pixel layers), and you add Figma-native text overlays.

### Path E — rerender (v0.1, planned)

When v0.1 ships, this command will let you change text in `trajectory.json` and regenerate just the affected layer:

```bash
uv run python -m design_agent.edit <run_id> --layer title --text "国宝回家·壹"
# regenerates only the title layer, re-subsets fonts, recomposites PSD/SVG/preview
```

See [ROADMAP.md](ROADMAP.md) for status.

---

## Extending the system

### Add a new tool

1. Create `design_agent/tools/<your_tool>.py`. The handler signature:

```python
from typing import Any
from ._contract import ToolContext, obs_ok, obs_error
from ..schema import ToolObservation

def your_tool(args: dict[str, Any], *, ctx: ToolContext) -> ToolObservation:
    # validate args
    # do work
    # mutate ctx.state if needed
    return obs_ok("did the thing", artifacts=["<path>"], next_actions=["<hint>"])
```

2. Register it in [`design_agent/tools/__init__.py`](../design_agent/tools/__init__.py):
   - Add a JSON schema entry to `TOOL_SCHEMAS` (description ends with the observation contract notice).
   - Add the handler to `TOOL_HANDLERS` dict.

3. Update [`prompts/planner.md`](../prompts/planner.md) to mention the tool, when to use it, and any constraints.

4. Re-run `python -m design_agent.smoke` — the registry assertion will catch typos.

### Modify the trajectory schema

1. Edit [`design_agent/schema.py`](../design_agent/schema.py).
2. Update [DATA-CONTRACT.md](DATA-CONTRACT.md) to match (this drifts easiest).
3. Bump `metadata.version` in [`runner.py`](../design_agent/runner.py).
4. Add a note to [DECISIONS.md](DECISIONS.md) under a new dated entry.
5. Re-run smoke — pydantic round-trip will catch break.

### Tweak the critic rubric

Edit [`prompts/critic.md`](../prompts/critic.md). The rubric weights live in the markdown. To change the pass threshold (currently `score ≥ 0.75`), edit the verdict-rules section in the prompt AND the threshold logic if it ever moves into Python (currently the model self-reports verdict).

### Switch LLM model (test cheaper alternatives)

Set env vars:

```bash
export PLANNER_MODEL="anthropic/claude-haiku-4-5"   # cheaper, see if planning still holds
export CRITIC_MODEL="anthropic/claude-sonnet-4-6"
uv run python -m design_agent.cli "..."
```

Both planner and critic still go through the same Anthropic SDK + tool-use protocol regardless of model.

### Force a verdict revision (to generate DPO data)

Edit [`prompts/critic.md`](../prompts/critic.md), tighten the pass threshold (e.g. `score ≥ 0.90 AND zero blockers`). Run a few briefs — more will hit `verdict: "revise"`, producing pre/post layer_graph snapshots in `critique_loop`.

---

## Useful one-liners

```bash
# Count trajectories
ls out/trajectories/*.json | wc -l

# Total cost spent so far (sum of estimates)
uv run python -c "
import json, glob
total = sum(json.load(open(p))['metadata']['estimated_cost_usd']
            for p in glob.glob('out/trajectories/*.json'))
print(f'\${total:.2f} across {len(glob.glob(\"out/trajectories/*.json\"))} runs')
"

# Find runs where critic gave any blocker
uv run python -c "
import json, glob
for p in glob.glob('out/trajectories/*.json'):
    t = json.load(open(p))
    blockers = [i for c in t['critique_loop'] for i in c['issues'] if i['severity'] == 'blocker']
    if blockers:
        print(f'{t[\"run_id\"]}: {len(blockers)} blocker(s)')
"

# Average wall time per layer count
uv run python -c "
import json, glob
data = [(len(json.load(open(p))['layer_graph']), json.load(open(p))['metadata']['wall_time_s'])
        for p in glob.glob('out/trajectories/*.json')]
for layers, wt in sorted(data):
    print(f'{layers:3d} layers → {wt:.0f}s')
"
```

---

## Troubleshooting

| Symptom | First thing to check | Reference |
|---|---|---|
| `ANTHROPIC_API_KEY missing` despite `.env` being filled | shell exports an empty value masking it | [GOTCHAS.md](GOTCHAS.md) |
| `404 OpenRouter HTML page` from planner | `base_url` includes `/v1` (it shouldn't) | [GOTCHAS.md](GOTCHAS.md) |
| `'Group' object has no attribute 'create_pixel_layer'` | psd-tools 1.11+ API change | [GOTCHAS.md](GOTCHAS.md) |
| `Image.save() got unexpected kwarg 'format'` after Gemini call | google-genai SDK Image type | [GOTCHAS.md](GOTCHAS.md) |
| SVG opens fine in browser but explodes in Figma | Figma SVG importer is broken | [GOTCHAS.md](GOTCHAS.md) |
| `BadRequestError: credit balance too low` from Anthropic | top up at console.anthropic.com — or use OpenRouter | [GOTCHAS.md](GOTCHAS.md) |
| Edited SVG text shows `□` for new characters | font subset doesn't include those glyphs | [GOTCHAS.md](GOTCHAS.md) |
| Planner doesn't call `finalize` and hits `max_planner_turns` | tighten `prompts/planner.md` workflow contract | [ARCHITECTURE.md](ARCHITECTURE.md) |
