---
name: ResearcherX landing
description: The marketing page at "/": ask your papers, get answers with receipts.
colors:
  accent: "oklch(53% 0.2 260)"
  accent-ink: "oklch(99% 0 0)"
  accent-wash: "oklch(94% 0.035 258)"
  ground: "oklch(99.2% 0.002 250)"
  ink: "oklch(20% 0.018 258)"
  muted-ink: "oklch(47% 0.02 258)"
  hairline: "oklch(91.5% 0.007 255)"
  panel: "oklch(96.8% 0.005 252)"
  accent-dark: "oklch(70% 0.15 256)"
  accent-ink-dark: "oklch(16% 0.02 260)"
  accent-wash-dark: "oklch(30% 0.06 258)"
  ground-dark: "oklch(16.5% 0.008 258)"
  ink-dark: "oklch(96.5% 0.004 250)"
  muted-ink-dark: "oklch(72% 0.018 255)"
  hairline-dark: "oklch(28% 0.012 258)"
  panel-dark: "oklch(20.5% 0.01 258)"
  pdf-paper: "#ffffff"
typography:
  display:
    fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif"
    fontSize: "2.5rem / 3.75rem (sm) / 4.25rem (lg)"
    fontWeight: 600
    lineHeight: 1.04
    letterSpacing: "-0.03em"
  headline:
    fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif"
    fontSize: "1.875rem / 2.75rem (sm)"
    fontWeight: 600
    lineHeight: 1.1
    letterSpacing: "-0.03em"
  title:
    fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif"
    fontSize: "1.5rem / 1.75rem (sm)"
    fontWeight: 600
    letterSpacing: "-0.03em"
  lead:
    fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif"
    fontSize: "17px"
    fontWeight: 400
    lineHeight: 1.625
  body:
    fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif"
    fontSize: "16px"
    fontWeight: 400
    lineHeight: 1.625
  label:
    fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 500
  caption:
    fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 400
  code:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace"
    fontSize: "12px"
    lineHeight: 1.9
  pdf-serif:
    fontFamily: "ui-serif, New York, Georgia, Times New Roman, serif"
    fontSize: "13.5px"
    lineHeight: 1.625
rounded:
  sm: "4px"
  md: "6px"
  lg: "8px"
  xl: "12px"
  2xl: "16px"
  full: "9999px"
spacing:
  gutter: "20px"
  gutter-sm: "32px"
  container: "1180px"
  section: "96px"
  section-sm: "128px"
  step-gap: "80px"
  step-gap-sm: "112px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.accent-ink}"
    typography: "{typography.label}"
    rounded: "{rounded.lg}"
    padding: "0 24px"
    height: "48px"
  button-primary-compact:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.accent-ink}"
    rounded: "{rounded.lg}"
    padding: "0 16px"
    height: "36px"
  button-secondary:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.lg}"
    padding: "0 24px"
    height: "48px"
  button-secondary-hover:
    backgroundColor: "{colors.panel}"
  citation-chip:
    backgroundColor: "{colors.accent-wash}"
    textColor: "{colors.accent}"
    rounded: "{rounded.sm}"
    padding: "0 4px"
    height: "18px"
  citation-chip-open:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.accent-ink}"
  user-bubble:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.accent-ink}"
    rounded: "{rounded.2xl}"
    padding: "10px 16px"
  product-frame:
    backgroundColor: "{colors.ground}"
    rounded: "{rounded.xl}"
  closing-panel:
    backgroundColor: "{colors.panel}"
    rounded: "{rounded.2xl}"
    padding: "96px 24px"
  logo-tile:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.accent-ink}"
    rounded: "{rounded.md}"
    size: "28px"
---

# Design System: ResearcherX landing

## Overview

**Creative North Star: "The Annotated Proof"**

The landing page is the research-assistant category standard played straight: calm, product-forward, and every claim demonstrated rather than asserted. It reads like a well-set paper with its sources pinned in the margin. A near-white ground, slate ink and hairline rules carry almost everything; one blue accent marks exactly the things that are evidence (citation markers, the user's own question, the followed source, the one primary action). The real app recording, shown full colour in a browser frame, is the proof; the illustrated steps below it follow one paper from library to manuscript.

Density is low and the rhythm is generous: one 1180px column, large vertical breathing room between sections, left-aligned prose beside framed product visuals. The page follows the visitor's system theme (prefers-color-scheme) unless they pick one with the nav's own theme button (remembered per browser as `rx.landing.theme`, applied through `data-theme` on the root before first paint); it never reads the app's toggle. Every colour exists as a light and a dark channel set.

**Scope.** This file describes the landing world only (route "/", everything under `.rx-landing`, the `site` palette). The app under `/admin` has its own token set in `frontend/src/app/globals.css` (ported from the Lovable prototype) and follows its own theme toggle. The two share Inter, the "R" logo tile, and a near-identical accent blue on purpose; they do not share tokens. Do not apply landing tokens to app screens, or app tokens to the landing.

**Key Characteristics:**
- Near-white (near-black in dark) ground, slate ink, hairline borders, no decorative colour.
- One blue accent, spent only on evidence and on the primary action.
- Inter for all interface and prose; serif and mono appear only inside rendered artifacts (the PDF page, LaTeX source).
- Product visuals in 12px-radius frames with one soft offset shadow.
- One authored motion: the followed source pulses once as its step scrolls in.

## Colors

A cool, nearly achromatic slate system (hue 250 to 260, chroma at or below 0.02) with a single saturated blue. Channels live on `.rx-landing` in `landing.css` and are consumed through the Tailwind `site` palette; the frontmatter values are those channels.

### Primary
- **Receipt Blue** (accent / accent-dark): the primary button, the logo tile, the user's chat bubble, an open citation marker, the accent phrase in the hero headline ("with receipts."), focus rings and text selection (at 22% alpha). Lighter and softer in dark mode so it holds contrast on the near-black ground. It is a near-match to the app's `--primary` (app light 55% 0.2 258, dark 64% 0.18 258), not an identical token.
- **Receipt Ink** (accent-ink / accent-ink-dark): text on Receipt Blue. White in light mode, near-black in dark.
- **Citation Wash** (accent-wash / accent-wash-dark): resting citation markers, the tinted row of the followed paper in every step, and the rest state of the trace pulse.

### Neutral
- **Proof Ground** (ground / ground-dark): the page background and the inside of product frames and tooltips.
- **Slate Ink** (ink / ink-dark): headings, emphasized lead-ins, product text.
- **Margin Grey** (muted-ink / muted-ink-dark): sublines, body copy of sections, captions, nav links at rest, icons.
- **Hairline** (hairline / hairline-dark): every border and rule: section dividers, frame borders, list separators, the secondary button outline, browser-frame dots.
- **Panel** (panel / panel-dark): one step off the ground: the browser frame's chrome, the closing CTA block, secondary-button hover.
- **PDF Paper** (pdf-paper): the rendered PDF page in the LaTeX step, with Tailwind neutral greys for its type. It stays white in dark mode.

### Named Rules
**The Evidence-Only Accent Rule.** Receipt Blue marks evidence (markers, the user's question, the followed source) and the single primary action. It never fills a section, a card, an icon, or a decorative shape.

**The Paper Stays Paper Rule.** A rendered artifact keeps its own colours: the PDF page is white with neutral type in both themes, because that is what the compiled paper looks like.

## Typography

**Display Font:** Inter (with ui-sans-serif, system-ui)
**Body Font:** Inter
**Label/Mono Font:** ui-monospace stack for LaTeX source only; ui-serif stack for the rendered PDF only.

**Character:** One sans at a few weights, tightly tracked in headings and relaxed in prose, the quiet register of Elicit and Perplexity. Semibold (600) is the heaviest weight in page copy; the logo tile's bold "R" is the one exception.

### Hierarchy
- **Display** (600, 2.5rem to 4.25rem, 1.04): the hero headline only, centered, max 4xl measure.
- **Headline** (600, 1.875rem to 2.75rem, 1.1): section headings, left-aligned, max-width about 28rem to 42rem. The closing CTA heading runs larger (2.25rem to 3.75rem) as the page's second display moment.
- **Title** (600, 1.5rem to 1.75rem): step titles.
- **Lead** (400, 17px, 1.625): section sublines and the trust paragraphs; the hero subline is 17px rising to 20px.
- **Body** (400, 16px, 1.625): step descriptions. The landing sets 16px on `.rx-landing`, overriding the app's 14px body.
- **Label** (500, 15px): buttons and the disclosure summary; nav links at 14px, regular weight.
- **Caption** (400, 13px, Margin Grey): figure captions, the hero proof line, footer, product-mock metadata; 12px for the smallest mock labels.

All h1 to h3 carry -0.03em tracking and balanced wrapping; paragraphs use pretty wrapping.

### Named Rules
**The Artifact Faces Rule.** Serif and mono exist only inside depicted artifacts (the PDF page, a .tex file). Page copy is Inter, always.

**The Bold Lead-In Rule.** Emphasis inside prose is a Slate Ink, 500-weight lead-in sentence set into Margin Grey body text, never a separate label above the paragraph.

## Layout

A single centered container (max 1180px) with 20px side gutters, 32px from the sm breakpoint. Sections breathe at 96px vertical padding, 128px from sm, divided by full-width hairlines. The hero opens 128px (160px from sm) below the top to clear the fixed 64px nav.

Content sections use a 5:7 two-column grid from lg (text left, product visual right, 64px gap), collapsing to one column below. The four steps stack at 80px (112px from sm). The hero is the only centered text block; everything after it is left-aligned. The hero recording is portrait (4:7) below 768px and 16:10 above, matching the phone and desktop recordings.

Breakpoints are Tailwind's: sm 640px, md 768px (nav collapses to a menu below it), lg 1024px (two-column steps).

## Elevation & Depth

Flat by default, with one lifted material: product visuals. Framed product shots, step visuals and the citation tooltip carry a single soft, offset shadow; everything else relies on hairlines and the one-step Panel tone. The nav gains an 85% ground fill and a medium backdrop blur once the page scrolls past 8px.

### Shadow Vocabulary
- **Product shot** (`box-shadow: 0 1px 2px oklch(0.2 0.02 258 / 0.06), 0 24px 60px -24px oklch(0.2 0.02 258 / 0.28)`): the hero browser frame, each step visual, the citation tooltip.

### Named Rules
**The Only-Product-Lifts Rule.** Only depictions of the product cast a shadow. Buttons, panels, text blocks and the disclosure stay flat.

## Shapes

Soft rectangles on a small ladder of radii: 4px for inline marks (citation chips, tinted spans), 6px for the logo tile, 8px for buttons and the tooltip, 12px for product frames and the disclosure, 16px for the closing CTA panel and chat bubbles, full circles for step numbers and status dots. The user bubble tucks its bottom-right corner to 6px, pointing at its speaker. Borders are always 1px Hairline.

Button, logo-tile and chip radii resolve through the Tailwind scale, which reads the app's `--radius` (8px) from globals.css; a change there moves these landing radii too.

## Components

### Buttons
Quiet and solid; the primary is the only filled object in its row.
- **Shape:** gently rounded (8px).
- **Primary:** Receipt Blue fill, Receipt Ink text, 15px medium, 48px tall with 24px side padding, optional trailing arrow at 16px. Compact nav variant is 36px tall, 16px padding, 14px.
- **Hover / Focus:** hover brightens the fill (brightness 110%); focus-visible is a 2px accent outline at 3px offset with 6px radius, page-wide.
- **Secondary:** transparent with a 1px Hairline outline and Slate Ink text; hover fills with Panel (with Ground on the Panel-coloured closing block).

### Chips
- **Style:** citation markers `[n]` as 18px-tall, 11px semibold tabular numerals on Citation Wash with Receipt Blue text, raised 3px off the baseline, with an enlarged invisible hit area.
- **State:** hover (mouse) or tap/Enter opens a tooltip; open state inverts to Receipt Blue fill. The tooltip is a 22rem Ground card with Hairline border, the product shadow, and paper title, locator (`Section > Subsection · p. N`), then passage.

### Cards / Containers
- **Corner Style:** 12px for product frames; 16px for the closing panel.
- **Background:** Ground inside product frames; Panel for browser chrome and the closing panel.
- **Shadow Strategy:** product frames only (see Elevation).
- **Border:** 1px Hairline.
- **Internal Padding:** 16px, 24px from sm, for product mocks; 96px vertical for the closing panel.

### Navigation
Fixed, 64px tall. Transparent over the hero; after 8px of scroll it takes an 85% Ground fill, a Hairline bottom border and backdrop blur. Logo tile plus 15px semibold wordmark left; 14px Margin Grey links turning Slate Ink on hover, then a compact primary button. Below 768px a 44px menu button opens a full-screen Ground sheet with 20px medium links separated by hairlines and a full-width 48px primary button; the blur is dropped while it is open.

### The Followed Source (signature)
One paper is tinted Citation Wash wherever it appears: its library row, its entry in the sources list, the opened passage title, and its `\cite{}` in the LaTeX source. When a step scrolls 45% into view, that tint pulses once from Receipt Blue at 50% alpha to the wash over 2.2s (`cubic-bezier(0.16, 1, 0.3, 1)`). Content is visible without the animation; under reduced motion the animation collapses to nothing.

### Step Marker
A 32px circle, 1px Hairline border, 13px medium tabular numeral in Margin Grey, above each step title.

### Disclosure
A native details element in a 12px Hairline-bordered box: 15px medium summary with a 13px "Show / Hide" affordance, opening to a two-column grid of 15px Margin Grey paragraphs above a Hairline rule. Technical depth lives here, behind the reader-facing copy.

### Browser Frame
The hero recording sits in a 12px frame with a 40px Panel chrome bar: three 10px Hairline dots and a centered 12px Margin Grey title. The recording plays full colour, muted, looped; reduced motion shows the poster instead.

## Do's and Don'ts

### Do:
- **Do** set colours through the `site` palette (`bg-site-*`, `text-site-*`) so both schemes resolve from the `.rx-landing` channels.
- **Do** show the product legibly and in full colour, in a frame with the product shadow.
- **Do** tint the followed source with Citation Wash in every visual that includes it.
- **Do** use the app's exact wording in demos: the refusal "The ingested documents do not cover this." and the locator `Section > Subsection · p. N`.
- **Do** keep icons to lucide at the default stroke, 16px to 20px, in Margin Grey.
- **Do** scope every landing style under `.rx-landing`.

### Don't:
- **Don't** put a dimmed or atmospheric full-bleed video behind the hero copy.
- **Don't** add eyebrow labels or kickers above headings.
- **Don't** arrange features as a grid of cards; state them once in prose or demonstrate them in a step.
- **Don't** spend Receipt Blue on decoration, section backgrounds, or icons.
- **Don't** use serif or mono outside a depicted artifact.
- **Don't** apply these tokens to the app under `/admin`, or the app's globals.css tokens to the landing.
