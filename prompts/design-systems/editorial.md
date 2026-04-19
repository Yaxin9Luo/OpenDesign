# Editorial — magazine-grade serif hierarchy

**Loudness: 3/10**. Quiet, refined, "worth reading." Picks from the NYT / The New Yorker / Stratechery vocabulary.

## Visual signature

Serif display headlines over cream backgrounds with narrow text columns and deliberate horizontal rules. Every element feels set by hand on a grid. Nothing moves; nothing gradients. The hierarchy does the work.

## Color tokens

- `--bg`: `#fbf9f4` — cream off-white, NOT pure white (reads warmer, less digital)
- `--bg-alt`: `#f2ede1` — slightly deeper cream
- `--bg-dark`: `#1a1a1a` — near-black for CTA / footer
- `--fg`: `#1a1a1a` — primary text
- `--fg-muted`: `#6b6358` — secondary text (warm gray)
- `--fg-inverse`: `#f5ece0` — on dark
- `--rule`: `#2a2824` — horizontal rules
- `--accent`: `#7a2020` — deep oxblood for drop caps / pull quotes. Override for brand.

## Typography

- Display family: a bundled serif if available (v1.0 uses `NotoSerifSC-Bold`). Aesthetic target: Playfair Display, Lora, EB Garamond, Freight Text.
- Body family: `NotoSansSC-Bold` (bundled). Aesthetic target: Inter, SF Pro, Lyon.
- Scale: hero 72–88px serif, section title 44–52px serif, body 18–20px sans, small 13px.
- Headline tracking: `-0.015em`. Italics appear in subheads (serif italic is the magazine signature).
- Weight: 700 serif headings, 400 body.
- Line-height: 1.2 display, 1.65 body.

## Section variants

- **hero**: centered, narrow column (max-width 720px), big serif headline, optional italic serif subhead beneath, single modest CTA.
- **features**: two-column text blocks on cream-alt bg, separated by thin horizontal rules (`--rule` at 1px).
- **cta**: dark section, serif centered headline, underlined link-style CTA (no button).
- **footer**: cream bg with a single 1px top rule, 13px serif, small caps for section labels.

## When to pick

Writing-heavy landings: essays, publications, research labs, thoughtful startups, academic projects, products whose pitch is a story. Anything where the reader is expected to read, not scan.

## When to avoid

Data-dense UIs, checklist-heavy product pitches, anything requiring a button-forward conversion flow. Editorial trades CTR for gravitas — don't use it when you need a hard funnel.

## DesignSpec tips

- `font_family: "NotoSerifSC-Bold"` on hero and features headlines; `"NotoSansSC-Bold"` on body and CTA
- `font_size_px`: 80 hero, 48 features title, 20 body, 14 footer
- `effects.fill`: `#1a1a1a` on cream, `#f5ece0` on dark
- Headlines can be longer (editorial tolerates 10+ word headlines) but subheads stay under 20 words
