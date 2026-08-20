import { IBM_Plex_Mono, IBM_Plex_Sans, Spectral } from "next/font/google";
import "./explorer.css";

/**
 * The Explorer subtree.
 *
 * Everything Explorer changes about the app's look is scoped to this one
 * wrapper: the Institution/Console palette (see explorer.css) and the three
 * typefaces the concept calls for. The rest of the app keeps its own tokens and
 * keeps rendering in Inter — `--font-inter` is untouched, and these three
 * variables exist only on this element and below it.
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

export default function ExplorerLayout({ children }: { children: React.ReactNode }) {
  return (
    <div
      className={`rx-ex ${plexSans.variable} ${plexMono.variable} ${spectral.variable}`}
    >
      {children}
    </div>
  );
}
