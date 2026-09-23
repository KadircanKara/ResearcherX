/**
 * The landing page's theme: the visitor's SYSTEM setting unless they pick one
 * with the page's own toggle, which is remembered per browser. Separate from
 * the app's theme (next-themes, under /admin) on purpose: the app defaults to
 * light regardless of the OS, while a first-time visitor should get their
 * system's look.
 *
 * Pure so the rules are testable; the provider in
 * components/landing/landing-theme.tsx does the DOM work.
 */
export type LandingTheme = "light" | "dark";

export const LANDING_THEME_KEY = "rx.landing.theme";

/** A stored choice, or null for "follow the system" (and for anything unrecognised). */
export function parseLandingTheme(value: string | null | undefined): LandingTheme | null {
  return value === "light" || value === "dark" ? value : null;
}

/** The theme to paint: an explicit choice wins, otherwise the system; dark until the system is known. */
export function resolveLandingTheme(
  stored: LandingTheme | null,
  systemPrefersLight: boolean | null,
): LandingTheme {
  if (stored) return stored;
  return systemPrefersLight === true ? "light" : "dark";
}

export function otherLandingTheme(theme: LandingTheme): LandingTheme {
  return theme === "light" ? "dark" : "light";
}

/**
 * Runs inline before the page paints, so a visitor who picked a theme never
 * sees the system one flash first. Kept here beside the key it reads.
 */
export const LANDING_THEME_BOOT = `try{var t=localStorage.getItem(${JSON.stringify(
  LANDING_THEME_KEY,
)});if(t==="light"||t==="dark")document.currentScript.parentElement.setAttribute("data-theme",t)}catch(e){}`;
