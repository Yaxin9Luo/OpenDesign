# Claymorphism — soft, puffy, consumer-friendly

**Loudness: 5/10**. Visually distinctive but calm. Rounded "3D-inflated clay" surfaces lit from above, muted pastel palette, generous line-height. Feels like touching soft plastic.

## Visual signature

Large rounded shapes (20–50px radii) with signature dual-inset + offset shadows that simulate inflated clay. Warm off-white backgrounds (never pure white), powder-pastel cards, rounded sans-serif type, playful but not loud.

## Color tokens

- `--bg`: `#f4f0ea` — warm off-white, avoid `#fff`
- `--bg-alt`: `#eee7dc` — slightly deeper
- `--bg-dark`: `#2a2530` — warm-dark, NOT pure black
- `--card-lavender`: `#dcd6f7`
- `--card-mint`: `#c8e6c9`
- `--card-peach`: `#ffd9b3`
- `--card-sky`: `#c7e9f8`
- `--fg`: `#3a2f4a` — deep warm purple-gray (softer than black)
- `--fg-muted`: `#7a6d8a`
- `--fg-inverse`: `#f4f0ea`
- `--accent`: `#a58cff` — soft lavender, override for brand
- `--shadow-clay`: `inset 3px 3px 6px rgba(255,255,255,0.6), inset -3px -3px 6px rgba(0,0,0,0.08), 8px 8px 20px rgba(170,150,200,0.25)`

## Typography

- Family: `NotoSansSC-Bold` (bundled). Aesthetic target: Nunito, Quicksand, DM Sans, Rounded SF.
- Scale: hero 72–88px, section title 44–48px, body 18–20px.
- Tracking: `0em` (rounded type dislikes tight tracking).
- Weight: 600–700 headings, 400–500 body. NEVER all-caps (breaks the soft feel).
- Line-height: 1.2 display, 1.65–1.7 body (generous to match the soft feel).

## Section variants

- **hero**: warm off-white bg, oversized puffy card containing the headline + soft-shadowed pill CTA. Mascot / illustration at right edge welcome (not provided by renderer; planner can hint with text).
- **features**: `--bg-alt` backdrop, 3 pastel clay cards (lavender / mint / peach) each with the signature dual-inset shadow.
- **cta**: warm dark section with a single oversized pill button, soft-shadowed to feel liftable.
- **footer**: rounded container floated above the page edge (`margin-bottom: 48px; border-radius: 32px;`) with minimal muted text.

## When to pick

Friendly consumer products, wellness / mindfulness, kids' education, onboarding flows, creative tools for non-technical users, playful marketing sites. Anything where warmth matters more than efficiency.

## When to avoid

Enterprise, finance, legal, dashboards, dense data UIs, "serious" B2B. Clay reads toy-like — it undersells anything that needs to feel authoritative.

## DesignSpec tips

- `font_size_px`: 80 hero, 48 features, 40 CTA
- `effects.fill`: `#3a2f4a` on light sections, `#f4f0ea` on dark
- Feature cards get different `accent_color` per card naturally (the CSS handles this via nth-of-type coloring — planner just writes the text)
- Headlines can be warmer / more informal: "Take a breath. We'll handle the rest." > "Enterprise-grade mindfulness"

## Imagery prompts (for `generate_image`)

Claymorphism images should look like they're **sculpted from warm soft clay**, photographed with soft overhead light. Always prefix prompts with:

> `soft 3D clay render, rounded pastel shapes, warm off-white background (#f4f0ea), gentle top-light, no hard shadows, cute stylized forms, claymorphism 3d aesthetic`

**Aspect ratios by slot**:
- hero image: `3:4` or `4:3` (portrait works well with the puffy card framing), `image_size: "2K"`
- feature icon: `1:1`, `image_size: "1K"` — the CSS renders these as 96px circles
- mid-section banner: `4:3` or `16:9`, `image_size: "1K"`

**Concrete prompt examples (milk tea brand)**:
- Hero: `soft 3D clay render, a warm milk tea in a rounded ceramic cup on a creamy pastel background, puffy plump forms, gentle top-light with no harsh shadows, claymorphism aesthetic, cute stylized, warm pastel palette — no text, no logos`
- Feature "手工糖浆": `soft 3D clay render, a small round copper pot with thick golden syrup bubbling, pastel peachy background, puffy stylized clay forms, claymorphism, cute miniature, top-down centered`
- Feature "茶农直采": `soft 3D clay render, stylized green tea leaves in cupped hands, soft mint pastel background, rounded clay forms, warm light, cute handmade feel`

**Avoid** in prompts: realism, photographic detail, metallic/glossy textures, dark moody palettes (clay should always feel warm/soft/pastel).
