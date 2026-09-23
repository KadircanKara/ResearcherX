/**
 * Which encoding of the hero clip a viewport gets.
 *
 * The clip is a recording of the real app (scripts/record-hero.mjs): the
 * library, a cited answer, a citation opened to its passage, the manuscript
 * compiled. One clip, three encodings, chosen by width alone -- 1920 wide for
 * large displays, 1280 by default, 854 for phones. Self-hosted under
 * public/landing, so re-running the script replaces them in place. Pure so
 * the thresholds are testable; the Hero component only reads the window
 * width and hands it here.
 */
export type HeroVideoTier = "sm" | "md" | "xl";

/**
 * The clip is recorded twice, once per app theme. The landing page follows
 * the VISITOR'S SYSTEM setting (prefers-color-scheme), not the app's theme
 * toggle: most visitors have never opened the app, and the app's own default
 * would show every one of them the light take regardless of their OS.
 * `null` -- the server render and first paint, before the media query is
 * read -- maps to the dark take, so hydration matches.
 */
export type HeroVideoTheme = "dark" | "light";

export function heroVideoTheme(systemPrefersLight: boolean | null): HeroVideoTheme {
  return systemPrefersLight === true ? "light" : "dark";
}

export function heroVideoSrc(theme: HeroVideoTheme, tier: HeroVideoTier): string {
  return `/landing/hero-${theme}-${tier}.mp4`;
}

/** First-second still for the theme, shown until the clip can play. */
export function heroPoster(theme: HeroVideoTheme): string {
  return `/landing/hero-${theme}-poster.jpg`;
}

export function heroVideoTier(viewportWidth: number): HeroVideoTier {
  if (viewportWidth < 768) return "sm";
  if (viewportWidth >= 1600) return "xl";
  return "md";
}
