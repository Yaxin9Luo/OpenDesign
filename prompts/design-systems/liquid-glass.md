# Liquid Glass — Apple-premium depth + reflection

**Loudness: 5/10**. Reads premium rather than loud. Depth-aware translucent glass with ambient color response — the glass looks like it "knows" what's underneath.

## Visual signature

Apple iOS 26 aesthetic: full-bleed imagery or subtle dynamic gradients, hairline borders, layered shadows (inset highlight + mid drop + soft ambient), spring-physics-ish micro-interactions. The chrome steps aside so content reads as dominant; all the quality cues are in the shadows and the half-pixel hairlines.

## Color tokens

- `--bg`: `radial-gradient(at 20% 0%, #1e293b 0%, #0f172a 50%, #020617 100%)` — deep nocturne default
- `--glass-bg-light`: `rgba(255, 255, 255, 0.72)` — for light-mode panels
- `--glass-bg-dark`: `rgba(28, 28, 30, 0.68)` — for dark-mode panels (default)
- `--glass-border`: `rgba(255, 255, 255, 0.2)` — 0.5px hairline
- `--blur`: `24px` with `saturate(1.8)` — the "vibrancy" trick
- `--fg`: `#f5f5f7` (Apple's near-white) — text on dark
- `--fg-muted`: `#98989d` (Apple gray)
- `--accent`: `#0a84ff` — system blue, overridable (indigo `#5e5ce6`, teal `#64d2ff`, pink `#ff375f` all work)
- `--shadow`: `0 1px 0 rgba(255,255,255,0.2) inset, 0 10px 40px rgba(0,0,0,0.18), 0 2px 6px rgba(0,0,0,0.08)` — triple-layer float

## Typography

- Family: `NotoSansSC-Bold` (bundled). Aesthetic target: SF Pro Display, Inter Tight, Geist.
- Scale: hero 80–120px (optical size kicks in large), section title 48–56px, body 18–20px.
- Tracking: `-0.025em` on large display (tighter than most), `0em` body.
- Weight: 600–700 display, 400–500 body. Mixed case, NEVER uppercase except 10px badges.
- Line-height: 1.05 display, 1.5 body.

## Section variants

- **hero**: deep nocturne gradient bg, centered display type (80–120px), thin hairline glass pill CTA. No image needed; the type + gradient carries.
- **features**: lighter gradient band, 2–3 elevated glass cards. On hover (CSS only, no JS) cards lift `translateY(-2px)` and their shadow deepens. Spring timing: `cubic-bezier(0.34, 1.56, 0.64, 1)`, 260ms.
- **cta**: returns to deep bg, single glass sheet centered, accent-colored text on glass. Large radius (32px).
- **footer**: thin hairline glass bar with 0.5px top border, muted small text.

## When to pick

Premium-feel products: AI creative tools, media / photography apps, music / video, design-forward brands, anything that needs to feel "expensive." Also good for Apple-adjacent ecosystems.

## When to avoid

Utility / budget / workhorse products (liquid-glass reads costly-and-knows-it), text-dense pages (same problem as glassmorphism), brands whose identity IS a color (liquid-glass is chrome-subordinate — the product is dominant). Also avoid if the brief's keyword is "bold" or "energetic."

## DesignSpec tips

- `font_size_px`: 104 hero (lean huge), 52 features, 44 CTA
- `effects.fill`: `#f5f5f7` on all dark sections, `#1d1d1f` if any light section
- Keep headlines poetic / short — liquid-glass prefers the typography to do the work: "Think. Different." over "Our AI platform empowers teams to..."
- `accent_color` overrides `--accent` for the system-colored CTAs; prefer iOS system colors (blue `#0a84ff`, indigo `#5e5ce6`, pink `#ff375f`, teal `#64d2ff`)
