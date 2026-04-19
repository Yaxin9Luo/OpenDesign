# Minimalist — the Stripe / Linear / Vercel house style

**Loudness: 3/10**. The quiet default. Use this for developer tools, SaaS, fintech, enterprise software, or any time you want the content to speak louder than the chrome.

## Visual signature

High-contrast on generous whitespace. Near-black text on pure white, precise spacing on a 4/8px grid, one restrained accent color, hairline borders over heavy shadows, zero decoration. The page feels engineered, not styled.

## Color tokens

- `--bg`: `#ffffff` (pure white) — default section background
- `--bg-alt`: `#fafafa` — features section (subtle contrast)
- `--bg-dark`: `#0a0a0a` — CTA / footer
- `--fg`: `#0a0a0a` — primary text on light
- `--fg-muted`: `#6b7280` — secondary text
- `--fg-inverse`: `#f5f5f5` — text on dark
- `--border`: `#e5e7eb` — hairline rules
- `--accent`: `#3b82f6` (blue-500) — single accent for links, primary CTAs. Override via `design_system.accent_color`.

## Typography

- Families: `NotoSansSC-Bold` (bundled) for both headings and body. If a custom sans-serif is loaded, Inter or SF Pro Display are the aesthetic target.
- Scale: hero headline 64–96px, section title 40–48px, body 18–20px, small 14px.
- Headline tracking: `-0.02em` (tight). Body tracking: `0em`.
- Weight: 600–700 headings, 400–500 body. No uppercase.
- Line-height: 1.1 for large display, 1.5–1.6 body.

## Section variants

- **hero**: white bg, one centered headline + 1-line subhead + one pill-shaped accent-colored CTA. No background image.
- **features**: `--bg-alt` backdrop, grid of 2–3 text blocks. No cards, no borders between items — spacing does the grouping.
- **cta**: dark (`--bg-dark`) section, centered bold headline, single inverse-colored button.
- **footer**: white bg with thin top border, 14px muted text.

## When to pick

Pick this when the brief is about a "professional" product: dev tools, SaaS, fintech, infrastructure, B2B, research platforms. Pick this when in doubt — it reads safe/serious everywhere.

## When to avoid

Avoid for consumer-playful products, creator-indie brands, media-heavy landings, or when the brief explicitly asks for "bold / fun / brutal / visually interesting." Minimalism on a playful brief feels cold.

## DesignSpec tips

- `font_size_px` ~96 on hero headline, 44 on features title, 36 on CTA headline
- `effects.fill` — use `--fg` tokens: `#0a0a0a` on light sections, `#f5f5f5` on dark
- Keep text short: hero headline ≤ 6 words, subhead ≤ 12 words, CTA ≤ 4 words

## Imagery prompts (for `generate_image`)

Minimalist images are **clean product photography or precise diagrammatic illustration**. Zero ornament, one subject, honest lighting, neutral background. Always prefix with:

> `clean minimalist product photography, single subject centered, pure white or very light gray background, soft even studio lighting, no ornament, no props, high-resolution, editorial-grade composition`

**Aspect ratios**:
- hero: `4:3` or `3:2`, `2K`
- feature icon: `1:1`, `1K` — CSS renders these as 120px squares (no decorative frame)
- mid banner: `16:9`, `1K`

**Concrete examples (dev tool brand)**:
- Hero: `clean minimalist product photo of a laptop displaying code editor UI, pure white background, soft top-light, no reflections, honest composition, editorial`
- Feature icon: `minimalist black-and-white line icon representing 'fast', thin strokes, single motif, solid white background`

**Avoid**: gradients, drop shadows, colorful backgrounds, illustrated/cartoon styles, busy compositions.
