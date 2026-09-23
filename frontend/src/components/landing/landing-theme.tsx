"use client";

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import {
  LANDING_THEME_BOOT,
  LANDING_THEME_KEY,
  otherLandingTheme,
  parseLandingTheme,
  resolveLandingTheme,
  type LandingTheme,
} from "@/lib/landing-theme";

type Ctx = { theme: LandingTheme; toggle: () => void };

const LandingThemeContext = createContext<Ctx>({ theme: "dark", toggle: () => {} });

export function useLandingTheme(): Ctx {
  return useContext(LandingThemeContext);
}

/**
 * The landing page's root. With no stored choice it carries no `data-theme`,
 * so landing.css follows prefers-color-scheme with no JavaScript at all; a
 * choice made with the toggle sets `data-theme`, which overrides the system.
 * The inline boot script applies a stored choice before first paint, which
 * is why the attribute may differ from the server render.
 */
export function LandingThemeRoot({ className, children }: { className: string; children: ReactNode }) {
  const [stored, setStored] = useState<LandingTheme | null>(null);
  const [systemPrefersLight, setSystemPrefersLight] = useState<boolean | null>(null);

  useEffect(() => {
    try {
      setStored(parseLandingTheme(window.localStorage.getItem(LANDING_THEME_KEY)));
    } catch {
      // Storage blocked: follow the system.
    }
    const query = window.matchMedia("(prefers-color-scheme: light)");
    const sync = () => setSystemPrefersLight(query.matches);
    sync();
    query.addEventListener("change", sync);
    return () => query.removeEventListener("change", sync);
  }, []);

  const theme = resolveLandingTheme(stored, systemPrefersLight);

  const toggle = useCallback(() => {
    const next = otherLandingTheme(theme);
    setStored(next);
    try {
      window.localStorage.setItem(LANDING_THEME_KEY, next);
    } catch {
      // Storage blocked: the choice lasts for this page view only.
    }
  }, [theme]);

  return (
    <LandingThemeContext.Provider value={{ theme, toggle }}>
      <div className={className} data-theme={stored ?? undefined} suppressHydrationWarning>
        <script dangerouslySetInnerHTML={{ __html: LANDING_THEME_BOOT }} />
        {children}
      </div>
    </LandingThemeContext.Provider>
  );
}
