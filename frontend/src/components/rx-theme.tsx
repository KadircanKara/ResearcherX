import { IBM_Plex_Mono, IBM_Plex_Sans, Spectral } from "next/font/google";
import "@/app/rx-theme.css";

/**
 * The wrapper every ported "Reading Room" screen renders inside.
 *
 * It owns the two things those screens must not each own a copy of: the
 * Institution/Console palette (declared on `.rx-theme` in `rx-theme.css`) and
 * the three typefaces the concept calls for. Both are scoped to this element
 * and below it — `--font-inter` is untouched, so the rest of the app keeps its
 * own tokens and keeps rendering in Inter.
 *
 * Screens pass their own class (`rx-ex`, `rx-gr`) for the differences that are
 * genuinely theirs: the shell cap, page padding, layout.
 *
 * The font loaders live here rather than in each route's layout because
 * next/font deduplicates per call site, not per family: two modules asking for
 * Spectral produce two font instances with two sets of CSS variables.
 */
const plexSans = IBM_Plex_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-plex-sans",
  display: "swap",
});

const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-plex-mono",
  display: "swap",
});

const spectral = Spectral({
  subsets: ["latin"],
  weight: ["300", "400", "500", "600"],
  variable: "--font-spectral",
  display: "swap",
});

export function RxTheme({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div
      className={`rx-theme ${plexSans.variable} ${plexMono.variable} ${spectral.variable}${
        className ? ` ${className}` : ""
      }`}
    >
      {children}
    </div>
  );
}
