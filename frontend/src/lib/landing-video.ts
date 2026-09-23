/**
 * Which encoding of the hero clip a viewport gets.
 *
 * The clip is a recording of the real app (scripts/record-hero.mjs): the
 * library, a cited answer, a citation opened to its passage, the manuscript
 * compiled. Chosen by width alone: 1920 wide for large displays, 1280 by
 * default, and below 768 a SEPARATE portrait recording made at a phone
 * viewport, because a downscaled desktop frame is unreadable on a phone. Self-hosted under
 * public/landing, so re-running the script replaces them in place. Pure so
 * the thresholds are testable; the Hero component only reads the window
 * width and hands it here.
 */
export type HeroVideoTier = "sm" | "md" | "xl";

/**
 * The clip is recorded twice, once per theme. Which one plays is the landing
 * page's resolved theme (lib/landing-theme.ts): the visitor's pick from the
 * nav toggle, otherwise their system setting.
 */
export type HeroVideoTheme = "dark" | "light";

export function heroVideoSrc(theme: HeroVideoTheme, tier: HeroVideoTier): string {
  return `/landing/hero-${theme}-${tier}.mp4`;
}

/**
 * The still shown until the clip plays, and instead of it under reduced
 * motion: the cited answer with a passage open, from the matching take --
 * phones get the phone recording's frame.
 */
export function heroPoster(theme: HeroVideoTheme, tier: HeroVideoTier): string {
  return tier === "sm" ? `/landing/hero-${theme}-sm-poster.jpg` : `/landing/hero-${theme}-poster.jpg`;
}

export function heroVideoTier(viewportWidth: number): HeroVideoTier {
  if (viewportWidth < 768) return "sm";
  if (viewportWidth >= 1600) return "xl";
  return "md";
}
