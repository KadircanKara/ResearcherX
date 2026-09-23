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

export const HERO_VIDEO: Record<HeroVideoTier, string> = {
  sm: "/landing/hero-sm.mp4",
  md: "/landing/hero-md.mp4",
  xl: "/landing/hero-xl.mp4",
};

/** First-second still, shown until the clip can play. */
export const HERO_POSTER = "/landing/hero-poster.jpg";

export function heroVideoTier(viewportWidth: number): HeroVideoTier {
  if (viewportWidth < 768) return "sm";
  if (viewportWidth >= 1600) return "xl";
  return "md";
}
