---
target: landing page
total_score: 19
max_score: 32
na_heuristics: 7,10
p0_count: 0
p1_count: 2
target_identity: "file:/Users/kadircan/dev/ResearcherX/frontend/src/components/landing/landing-page.tsx"
target_fingerprint: "sha256:2f83ea22d1896630103fe9af7411be0d06a262f2ccdfe2670c561cc48dd24851"
target_path: /Users/kadircan/dev/ResearcherX/frontend/src/components/landing/landing-page.tsx
timestamp: 2026-09-23T15-26-16Z
slug: frontend-src-components-landing-landing-page-tsx
---
Method: dual-agent (A: design review · B: detector + browser)

## Design Health Score
| # | Heuristic | Score | Key Issue |
|---|---|---|---|
| 1 | Visibility of System Status | 2 | Citation chip opens and closes on the same tap on touch |
| 2 | Match System / Real World | 2 | Pipeline jargon for grad students (hybrid retrieval, cross-encoder, section-bounded chunking) |
| 3 | User Control and Freedom | 2 | Autoplay video and looping caret with no pause control |
| 4 | Consistency and Standards | 2 | Demo refusal/locator differ from product wording; cards invert on hover but are not links |
| 5 | Error Prevention | 3 | CTA drops strangers into /admin with no login warning |
| 6 | Recognition Rather Than Recall | 3 | Clear anchor nav, consistent CTA |
| 7 | Flexibility and Efficiency | n/a | Landing page |
| 8 | Aesthetic and Minimalist Design | 2 | App recording behind the copy; four claims repeated across four sections |
| 9 | Error Recovery | 3 | Black fallback when video fails; static headline under reduced motion |
| 10 | Help and Documentation | n/a | Landing page |
| **Total** | | **19/32** | **Acceptable (59%)** |

## Design Specificity
Mixed. Citation demo, LaTeX SyncTeX demo, "with receipts" copy and drone-fleet content are authored for ResearcherX. Hero and frame are category-interchangeable: black/white Inter with italic-serif accent word, full-bleed greyscale video with grain and scrim, tracked-caps eyebrows on every section, 01/02/03 steps, invert-on-hover card grids, pill CTAs, tech-tag strip, centred final CTA.
Detector: CLI 0 findings (styles live in landing.css / render-time). In-page: 6 — hero-eyebrow-chip (hero.tsx:154), all-caps-body x2 (hero.tsx:154, :192; labels, low severity), blinking-cursor (hero.tsx:52; reduced-motion handled), buried-raster (grain SVG; mostly false positive), tiny-text 11px at 45% opacity (latex-section.tsx:52; real).

## Priority Issues
- [P1] Hero video fights the copy and hides the best evidence. Light take ~1.8:1 body contrast on mobile; cited answer runs through the headline at ~14s; second logo and cursor visible behind nav. Fix: solid hero, recording as a framed full-colour product shot. Command: /impeccable layout, /impeccable quieter
- [P1] Citation chips fail on first tap on touch; chips 30x20, Menu button 41x11. Fix: hover only for mouse, tap toggles, 44px targets, outside-tap close. Command: /impeccable harden
- [P2] Demo breaks fixed product wording (refusal and locator format). Command: /impeccable clarify
- [P2] Jargon and repetition instead of researcher outcomes; orphan Features card. Command: /impeccable distill
- [P2] Read-to-write positioning buried; LaTeX only in section 5, never tied to the cited passage. Command: /impeccable shape

## Persona Red Flags
Jordan: unexplained jargon in hero strip; no word on cost/sign-up/privacy; app opens into a seeded user's project. Riley: demo wording differs from app; hover-inverting cards do nothing; shared no-login workspace; unsourced "under a minute for twenty pages". Casey: dead first tap on chips; 41x11 Menu target; 1.8:1 light hero; stacked LaTeX demo with hover-only instructions; autoplay video on cellular. Dr. Priya (postdoc, Zotero + Overleaf): no Zotero/BibTeX story, no privacy/model statement, evals never shown.

## Minor Observations
.reveal starts at opacity 0 (invisible without JS); "All rights reserved" under "Source on GitHub"; one headline missing its period; eyebrow+h2 on every section; final CTA has no secondary action; 11px/45% instruction text in LaTeX section.

## Questions to Consider
What if the hero were the citation demo itself, live and in colour? Could one drone-fleet thread (question, receipt, \cite, PDF) replace six feature sections? Would the real groundedness eval numbers persuade academics more than three principle cards?
