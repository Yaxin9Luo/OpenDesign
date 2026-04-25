# Deck — Academic Editorial — paper-talk gravitas

**Loudness: 3/10**. Quiet, refined, NeurIPS/ICLR/CVPR plenary aesthetic. The deck looks like Stratechery + The New Yorker met an academic talk slide deck. Cream paper background, oxblood accent, PlayfairDisplay display + Inter body — set by hand on a grid.

## When to pick

**DEFAULT for paper2deck.** When `artifact_type=deck` AND `ingest_document` ran successfully on a research paper, set `deck_design_system.style="academic-editorial"`. Use cases:

- NeurIPS / ICLR / CVPR / ACL / ICML talks
- Lab readouts, group meeting presentations
- Thesis defense / proposal decks
- arXiv companion talks
- Industry research presentations to academic audience

## When to avoid

Pitch decks (investor / marketing / brand launch) — those want the future commercial-grade `deck-pitch` system. Tutorial / lecture decks for undergrads — too restrained; use the future `deck-clean` once it ships.

## Visual signature

- Cream `#FAF7F0` background — NOT white (reads warmer, less digital)
- Single oxblood `#7F1D1D` accent — section labels, divider blocks, badge fill, footer rule. Used **consistently** in those places, **nowhere else**.
- PlayfairDisplay-Variable for titles (display serif, magazine grade)
- Inter-Variable for body / footer / labels (clean sans, hierarchical)
- Hairline rules under titles; thicker accent block on section dividers; thin slate footer rule
- No gradients, no shadows, no decorative shapes other than the accent stripes/blocks defined in the master

## Layout vocabulary

The template at `assets/deck_templates/academic-editorial.pptx` carries 6 layouts. Each spec slide MUST set `slide.role` to one of:

| `slide.role` | layout idx | When to use | Example slide names |
|---|---|---|---|
| `cover` | 0 | First slide. Title + authors + venue badge + hero image. | `slide_01` |
| `section_divider` | 1 | Visual breakpoint between major content blocks. Big section title + number. | "Method →", "Results →", "Discussion →" |
| `content` | 2 | Text-only or text-dominant content slide. | "Background", "Three takeaways" |
| `content_with_figure` | 3 | Method/architecture/qualitative slide. Body left + figure right. | Slides referencing `ingest_fig_NN` |
| `content_with_table` | 4 | Benchmarks. Body left + native PPTX table right. | "Benchmark leadership" |
| `closing` | 5 | Last slide. "Thank you / Q&A / arXiv link". | `slide_NN` |

## Slide-count guidance (12-slide academic talk)

For a 12-slide research talk, expect this distribution:

- **1 cover** (the first slide)
- **2-3 section_divider** between major content blocks
- **8-9 content / content_with_figure / content_with_table combined**, of which **≥4 reference `ingest_fig_NN`** (paper figures, NOT NBP — see paper2deck imagery policy in `prompts/planner.md`)
- **1 closing**

For a 16-slide research talk (NeurIPS oral): expand the content tier; keep dividers at 3.

## Template slot vocabulary

Every `kind: "text"` / `"image"` / `"table"` child of a slide should set `template_slot` to the name of the template shape it fills. Slot names per layout:

- **cover (0)**: `title` (PlayfairDisplay 56pt), `authors` (Inter 22pt muted), `badge` (Inter 13pt cream-on-oxblood), `image_slot` (right 50% hero)
- **section_divider (1)**: `section_number` (Inter 24pt oxblood, e.g. "01 ·"), `title` (PlayfairDisplay 120pt, e.g. "Method →"), `subtitle` (Inter 18pt muted italic preview line)
- **content (2)**: `section_label` (Inter 14pt oxblood uppercase, e.g. "01 · METHOD"), `title` (PlayfairDisplay 36pt), `body` (Inter 22pt) + auto `footer` + auto `slide_number`
- **content_with_figure (3)**: same chrome as content + `image_slot` (right 768px)
- **content_with_table (4)**: same chrome as content + `table_anchor` (right 904px — anchor's bbox wins; planner doesn't position the table)
- **closing (5)**: `title` ("Thank you", PlayfairDisplay 96pt), `subtitle` ("Q&A"), `links` (oxblood — see real-URLs rule below)

`footer` and `slide_number` are auto-populated by the renderer (paper title + slide N/total). Don't write children for those slots — the renderer handles them.

### `links` slot must be REAL URLs, not placeholders (v2.5.2.2)

The closing-slide `links` slot must contain real URLs derived from the ingested paper, NOT placeholder labels like `"Paper · Code · Demo"`. The audience cannot click decorative text on a slide; placeholders ship a debug artifact, not a deliverable.

Format: `"arxiv.org/abs/<id> · github.com/<org>/<repo> · <project-page-url>"` (use ` · ` middle-dot separators, drop "https://" prefix to save horizontal space — readers infer it).

Sources to mine from the ingest manifest / paper text:
- arxiv URL: from the paper's first-page DOI block, or the abstract's `arXiv:YYYY.NNNNN` token
- github URL: from the abstract / introduction / acknowledgments line starting `Code:` or `https://github.com/...`
- project page: from acknowledgments or footer of the paper

If only one URL is known, emit just that one (e.g. `"arxiv.org/abs/2026.05421"`). Do NOT pad with placeholder labels. If NO URLs can be resolved from ingest, omit the `links` child entirely — the closing slide degrades to "Thank you / Q&A" gracefully.

Bad → Good:
- `"Paper · Code · Demo"` → `"arxiv.org/abs/2026.05421 · github.com/meituan/longcat-next"`
- `"https://...."` → `"longcat-next.github.io"` (no protocol)
- `"see the paper"` → omit the `links` child entirely

### `section_label` format contract (v2.5.2.2)

The `section_label` slot MUST follow the format `"NN · UPPERCASE_TOPIC"`. Bare numbers like `"01"` defeat the slide's wayfinding purpose — the audience has no clue which chapter of the talk they're in.

Examples:
- `"01 · MOTIVATION"` (problem statement slides)
- `"02 · METHOD"` (architecture / algorithm slides — consecutive method slides share `"02 · METHOD"`, no per-slide variants)
- `"03 · RESULTS"` (benchmarks / quantitative)
- `"04 · ANALYSIS"` (ablations / scaling / qualitative)
- `"05 · CONCLUSION"` (takeaways)

Number prefix tracks chapters, not slide indices. So a 12-slide deck typically has 4-5 distinct `section_label` values, each repeated across that chapter's 2-3 slides. Caps + `· ` middle-dot separator are non-negotiable — they're the typographic signal the audience reads as "section header".

Bad → Good:
- `"01"` → `"01 · MOTIVATION"`
- `"Method"` → `"02 · METHOD"`
- `"02 · method overview"` → `"02 · METHOD"` (uppercase, drop slide-specific suffix; that goes in title)

## Spec-shape example (one method slide referencing a paper figure)

```json
{
  "layer_id": "slide_05",
  "name": "method_overview",
  "kind": "slide",
  "z_index": 5,
  "role": "content_with_figure",
  "children": [
    {"layer_id": "slide_05_label", "name": "section_label", "kind": "text", "z_index": 10,
     "template_slot": "section_label", "text": "02 · METHOD"},
    {"layer_id": "slide_05_title", "name": "title", "kind": "text", "z_index": 10,
     "template_slot": "title", "text": "DiNA: Discrete Native Autoregression"},
    {"layer_id": "slide_05_body", "name": "body", "kind": "text", "z_index": 10,
     "template_slot": "body",
     "text": "Tokenizes vision, text, and audio into a shared discrete vocabulary.\nAutoregressively predicts next token across modalities."},
    {"layer_id": "ingest_fig_03", "name": "method_diagram", "kind": "image", "z_index": 5,
     "template_slot": "image_slot"}
  ]
}
```

Note the `image` child's `layer_id` is `ingest_fig_03` (references the ingested paper figure by its existing layer_id — see hard rule #5 in `prompts/planner.md` paper2deck section). `template_slot: "image_slot"` tells the renderer to use the cover layout's `image_slot` shape's bbox.

## Imagery prompt prefix (cover NBP only)

The cover slide's hero image is the only place where NBP is acceptable on a paper deck. Style prefix:

> `editorial academic photography, abstract pastel gradient, soft chiaroscuro, light-on-dark composition, no text, no characters, suggesting [paper-topic-hint]`

For multimodal-generative papers: hint = "modal weave, emergent structure, layered tokens".
For systems / efficiency papers: hint = "clean architecture, quiet structure, scalability".
For theory papers: hint = "minimal mathematical structure, paper texture, restrained palette".

If the paper has a strong Fig. 1 (architecture diagram), prefer `ingest_fig_01` for the cover hero instead of NBP — `template_slot: "image_slot"` works for both.

## Negative constraints

- DO NOT override the master's `#FAF7F0` cream background. The renderer leaves the cream rectangle alone; planner-supplied `kind: "background"` children will overlay the cream and break the visual identity.
- DO NOT introduce new fonts. PlayfairDisplay-Variable + Inter-Variable only. The template's `font_family` strings are `Playfair Display` (display serif) and `Inter` (sans).
- Single accent color `#7F1D1D` everywhere — no per-slide palette swaps. If `accent_color` override is set on `DeckDesignSystem`, the renderer applies it uniformly.
- DO NOT skip `slide.role` on any slide node — defaults to `content` if missing, but explicit declarations make trajectories cleaner.
- DO NOT auto-generate divider slides — the planner declares them explicitly.
- DO NOT write `footer` / `slide_number` children — the renderer auto-fills both.
