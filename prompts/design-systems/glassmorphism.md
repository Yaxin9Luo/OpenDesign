# Glassmorphism — frosted panels over color fields

**Loudness: 6/10**. The chrome is quiet; the backdrop is loud. Pick a vivid aurora / magenta / cyan gradient and float clean glass cards on it.

## Visual signature

Full-page backdrop gradients with semi-transparent frosted-glass panels stacked in depth. The glass layer has a subtle inner highlight, a thin 1px border, and a soft drop shadow for separation.

## Color tokens

- `--bg-gradient`: `linear-gradient(135deg, #ff6b9d 0%, #c471ed 40%, #12c2e9 100%)` — aurora (pink → violet → cyan)
- `--glass-bg`: `rgba(255, 255, 255, 0.12)` — default glass tint
- `--glass-bg-strong`: `rgba(255, 255, 255, 0.22)` — heavier panels
- `--glass-border`: `rgba(255, 255, 255, 0.22)` — hairline
- `--blur`: `20px` — backdrop-filter amount
- `--fg`: `#f8fafc` — near-white text
- `--fg-muted`: `rgba(248, 250, 252, 0.72)`
- `--accent`: `#38bdf8` — sky-cyan for CTA buttons. Override via `accent_color`.
- `--shadow`: `0 8px 32px rgba(0, 0, 0, 0.18)`

## Typography

- Family: `NotoSansSC-Bold` (bundled). Aesthetic target: Inter, Manrope, Geist.
- Scale: hero 72–96px, section title 44–52px, body 18–20px, labels 13px.
- Tracking: `-0.02em` headlines.
- Weight: 600 headings, 400–500 body. Mixed case.
- Text over glass must be high-contrast — default to white-on-color or dark-on-light-glass.

## Section variants

- **hero**: gradient backdrop covers the full page max-width. Centered glass card (`--glass-bg-strong`) holds headline + subhead + accent CTA. Feels floating.
- **features**: same gradient continues; 3 glass cards side-by-side, each `--glass-bg` with 24px backdrop-filter, soft drop shadow.
- **cta**: deeper gradient band, wide translucent glass panel with inset accent button.
- **footer**: thicker glass bar at bottom (more blur, `--glass-bg-strong`), muted text.

## Accessibility note

The HTML renderer auto-adds an `@media (prefers-reduced-transparency: reduce)` fallback: if the user's OS has reduced transparency, glass panels swap to solid `rgba(20, 20, 30, 0.92)` with no blur.

## When to pick

Modern SaaS with energy, AI/ML products (gradients are on-brand for AI), creative/design tools, premium-feel consumer apps, overlay / modal-heavy UIs. Pick when the product has color energy and you want the page to match.

## When to avoid

Text-heavy docs (glass cards can't hold essays), low-end devices (backdrop-filter is GPU-expensive), monochrome brands (glass needs a vivid backdrop to work — on gray it looks like nothing), accessibility-critical UIs (transparency reduces contrast).

## DesignSpec tips

- `font_size_px`: 88 hero, 48 features, 40 CTA
- `effects.fill`: `#f8fafc` throughout — the gradient backdrop is the color story, text stays white
- Keep glass panels' text short — 3–5 lines max per card
- Use `accent_color` to shift the CTA button color only (the gradient palette stays aurora unless you override CSS)
