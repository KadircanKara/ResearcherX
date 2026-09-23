import "@/app/landing.css";
import { Nav } from "./nav";
import { Hero } from "./hero";
import { Thread } from "./thread";
import { Outcomes } from "./outcomes";
import { FinalCta } from "./final-cta";
import { Footer } from "./footer";
import { LandingThemeRoot } from "./landing-theme";

/**
 * The marketing page at "/". Its world is the research-assistant category
 * standard (benchmarks: Elicit, Perplexity), in the app's own blue and Inter,
 * following the visitor's system theme unless they pick one with the nav toggle. The story is one question followed
 * from library to manuscript.
 */
export function LandingPage() {
  return (
    <LandingThemeRoot className="rx-landing bg-site-bg text-site-fg min-h-screen">
      <Nav />
      <main>
        <Hero />
        <Thread />
        <Outcomes />
        <FinalCta />
      </main>
      <Footer />
    </LandingThemeRoot>
  );
}
