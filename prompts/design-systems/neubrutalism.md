# Neubrutalism — loud, raw, fearless

**Loudness: 10/10**. The maximum setting. Thick black outlines, hard zero-blur shadows, saturated candy colors, zero subtlety.

## Visual signature

Every component is a bordered block sitting on a loud colored plane, with a solid-black offset shadow that says "I am a physical object." Hero / features / CTA each get their own color slab. The page shouts.

## Color tokens

- `--bg`: `#fef3c7` — warm yellow hero backdrop (override per brand)
- `--bg-alt`: `#ffffff` — crisp white for features
- `--bg-cta`: `#ff6fb5` — hot pink for CTA (override via `accent_color`)
- `--bg-footer`: `#000000` — pure black
- `--fg`: `#000000` — black borders + text
- `--fg-inverse`: `#ffffff` — text on black sections
- `--accent`: `#ff6fb5` — hot pink, overridable
- `--border`: `#000000` at 4px — signature thickness
- `--shadow`: `8px 8px 0 #000000` — hard offset, zero blur

## Typography

- Family: `NotoSansSC-Bold` (bundled). Aesthetic target: Space Grotesk, Archivo Black, Druk, Bricolage.
- Scale: hero 80–120px (huge), section title 56px, body 18–20px, labels 12–14px uppercase.
- Tracking: `-0.02em` on headings, `0.05em` on uppercase labels.
- Weight: 800–900 headings, 500 body. Headlines often UPPERCASE.
- Line-height: 1.0 display (very tight), 1.4 body.

## Section variants

- **hero**: loud yellow/pink slab, massive 100px+ headline all caps, chunky pill CTA in inverse color with black border + offset shadow.
- **features**: white bg with 3 cards — each a different saturated color (pink / lime / blue / orange), each with 4px black border + 6px black offset shadow. Uppercase section labels.
- **cta**: bright pink slab, oversized centered headline, one giant black button with reversed shadow (yellow/lime shadow, black fill).
- **footer**: pure black bg, white text, no borders (footer gets to rest a little), big interactive tiles on hover.

## When to pick

Indie products with personality: developer tools (Fly, Gumroad, Basecamp), creator-economy products, art/fashion portfolios, bold startup launches. Anything where you want visitors to remember you after 5 seconds.

## When to avoid

Finance, legal, medical, enterprise, anything that needs to feel "safe" or "serious." Neubrutalism on a bank's page would be a PR disaster. Also avoid if the brief's tone is calm or meditative.

## DesignSpec tips

- Headlines in UPPERCASE (it's part of the look — set the text that way directly)
- `font_size_px`: 112 hero, 56 features, 72 CTA, 14 footer
- `effects.fill`: `#000000` on light sections, `#ffffff` on dark
- Hero headline as short and loud as possible ("BUILD LOUD", "SHIP UGLY", "POSTERS, NOT POSTING")
- Features titles with quippy edge; subheads direct / aggressive

## Imagery prompts (for `generate_image`)

Neubrutalism images are **loud flat graphics or high-contrast photography**. Think sticker art, risograph prints, punk zine aesthetic. Always prefix with:

> `flat bold graphic illustration, neubrutalism aesthetic, thick black outlines, saturated candy colors (hot pink, electric yellow, Klein blue, lime), zero gradients, zero shadows inside the image, sharp geometric shapes, risograph-print feel, punk zine energy`

**Aspect ratios**:
- hero: `1:1` or `4:5`, `2K` — bold square works with the grid-block aesthetic
- feature icon: `1:1`, `1K` — CSS frames them with black border + offset shadow
- mid banner: `16:9`, `1K`

**Concrete examples (indie devtool)**:
- Hero: `flat bold graphic of a lightning bolt on hot pink background, thick black outline, neubrutalism, risograph style, no gradients, no shadows`
- Feature icon: `flat bold line icon of a gear wheel on electric yellow, thick black stroke, neubrutalism sticker aesthetic`

**Avoid**: realistic photography, soft gradients, glossy textures, anything pastel or calm.
