# Landing Design Systems

LongcatDesign ships six pre-tuned landing-page design systems. The planner picks one per landing artifact based on the brief; the HTML renderer loads the matching CSS file from `assets/design-systems/` and inlines it into the generated page.

All systems share the same layout primitives (`<main class="ld-landing">`, `<section class="ld-section" data-section-variant="hero|features|cta|footer">`, `<div class="layer text">`) — they differ in tokens (colors, fonts, shadows, radii) and section-variant styling. This means **a single DesignSpec can swap styles by changing just `design_system.style`**, with no schema changes.

## Loudness ladder (for brief → style matching)

| Loudness | Style | When to pick |
|---|---|---|
| 3 | [minimalist](./minimalist.md) | Stripe/Linear/Vercel feel. Developer tools, SaaS, utility products, "serious" startups. Default. |
| 3 | [editorial](./editorial.md) | NYT / The New Yorker. Long-form content, publications, "thoughtful" brands, research labs. |
| 5 | [claymorphism](./claymorphism.md) | Friendly, playful, consumer. Education, wellness, kids, creative tools. |
| 5 | [liquid-glass](./liquid-glass.md) | Apple-premium. Media-rich products, AI/creative tools, design-first brands. |
| 6 | [glassmorphism](./glassmorphism.md) | Modern SaaS, AI products, overlay UI. Needs vivid backdrop. |
| 10 | [neubrutalism](./neubrutalism.md) | Indie / creator / devtool with attitude. Portfolios, experimental brands. |

## How the planner picks

Read the brief and use these cues:

- **"fintech", "enterprise", "dashboard", "data platform"** → `minimalist` (or occasionally `editorial` for data-journalism vibes)
- **"magazine", "publication", "essay", "study", "research", "journal"** → `editorial`
- **"wellness", "mental health", "kids", "playful", "onboarding", "cute"** → `claymorphism`
- **"premium", "Apple-like", "media", "photography app", "AI creative"** → `liquid-glass`
- **"AI", "SaaS tool", "modern", "ML"** + brief has gradient/color energy → `glassmorphism`
- **"indie", "devtool", "portfolio", "personal", "bold", "no-BS"** → `neubrutalism`

If the brief is ambiguous, **default to `minimalist`**. The "correct" pick is almost always the one that won't surprise the user.

## How a style is applied

In the DesignSpec, declare the style once:

```json
{
  "artifact_type": "landing",
  "design_system": {
    "style": "neubrutalism",
    "accent_color": "#ff6fb5"
  },
  "layer_graph": [...]
}
```

`design_system.accent_color` optionally overrides the style's default primary accent (useful when a brand color is known). Unknown styles fall back to `minimalist`.

## What each style file covers

Each `<style>.md` in this directory has the same five sections:

1. **Visual signature** — the one-line hook
2. **Color tokens** — what's in the CSS file
3. **Typography** — font families, scale, tracking
4. **Section variants** — how hero/features/cta/footer look in this style
5. **When to pick / avoid**

Read the style-specific file before composing a DesignSpec in that style so the content-level choices (headline length, font-size-px, fill) harmonize with what the CSS expects.
