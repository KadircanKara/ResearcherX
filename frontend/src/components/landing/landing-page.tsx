import { Instrument_Serif } from "next/font/google";
import "@/app/landing.css";
import { Nav } from "./nav";
import { Hero } from "./hero";
import { HowItWorks } from "./how-it-works";
import { CitationDemo } from "./citation-demo";
import { Features } from "./features";
import { LatexSection } from "./latex-section";
import { Principles } from "./principles";
import { FinalCta } from "./final-cta";
import { Footer } from "./footer";

/**
 * The marketing page at "/". Designed in Lovable (project 81504d96) and ported
 * here so one Next app serves both the page and the app under /admin.
 *
 * Inter comes from the root layout. The one other face, the italic serif on
 * the hero and the principles, is loaded here and scoped to this tree via
 * `--font-instrument-serif`, which `landing.css` folds into `--font-serif` on
 * `.rx-landing` only. The app's own tokens are never touched.
 */
const instrumentSerif = Instrument_Serif({
  subsets: ["latin"],
  weight: "400",
  style: ["normal", "italic"],
  variable: "--font-instrument-serif",
  display: "swap",
});

export function LandingPage() {
  return (
    <div className={`rx-landing bg-lp-ink min-h-screen ${instrumentSerif.variable}`}>
      <Nav />
      <main>
        <Hero />
        <HowItWorks />
        <CitationDemo />
        <Features />
        <LatexSection />
        <Principles />
        <FinalCta />
      </main>
      <Footer />
    </div>
  );
}
