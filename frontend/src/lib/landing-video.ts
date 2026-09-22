/**
 * Which encoding of the hero clip a viewport gets.
 *
 * One clip, three encodings, chosen by width alone. The 1080p file is 20.7 MB
 * and only earns its weight on a wide display; the 540p file (4.2 MB) is the
 * default and the 360p file (2.0 MB) serves phones. Pure so the thresholds are
 * testable; the Hero component only reads the window width and hands it here.
 */
export type HeroVideoTier = "sm" | "md" | "xl";

export const HERO_VIDEO: Record<HeroVideoTier, string> = {
  sm: "https://videos.pexels.com/video-files/3051492/3051492-sd_640_360_25fps.mp4",
  md: "https://videos.pexels.com/video-files/3051492/3051492-sd_960_540_25fps.mp4",
  xl: "https://videos.pexels.com/video-files/3051492/3051492-hd_1920_1080_25fps.mp4",
};

/** Credit shown in the footer; the clip is Pexels-licensed, credit optional. */
export const HERO_VIDEO_CREDIT = "Hero footage by Dan Cristian Pădureț via Pexels";

export function heroVideoTier(viewportWidth: number): HeroVideoTier {
  if (viewportWidth < 768) return "sm";
  if (viewportWidth >= 1600) return "xl";
  return "md";
}
