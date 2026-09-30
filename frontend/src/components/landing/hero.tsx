"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowRight } from "lucide-react";
import { routes } from "@/lib/routes";
import { usePrefersReducedMotion } from "@/hooks/use-reveal";
import {
  heroPoster,
  heroVideoAspect,
  heroVideoSrc,
  heroVideoTier,
  type HeroVideoTier,
} from "@/lib/landing-video";
import { scrollToHash } from "./scroll-to";
import { useLandingTheme } from "./landing-theme";

/**
 * Headline, one-sentence offer, two actions, then the real app at work.
 *
 * The recording (scripts/record-hero.mjs) is the proof, so it is shown the
 * way a product shot is shown: full colour, in a browser frame, at the
 * content width -- never dimmed behind the copy.
 */
export function Hero() {
  const reduced = usePrefersReducedMotion();
  const [videoOk, setVideoOk] = useState(true);
  // "md" on the server and for the first paint, so the markup the client
  // hydrates matches; the real tier lands in the effect below.
  const [tier, setTier] = useState<HeroVideoTier>("md");
  // The page's resolved theme: the visitor's pick from the nav toggle, else
  // their system setting; dark until known, the same file the server rendered.
  const { theme } = useLandingTheme();
  // The frame's shape is the clip's shape, measured from the file once it
  // loads, so no window size crops it; until then, the recording viewport's.
  const [measured, setMeasured] = useState<{ tier: HeroVideoTier; ratio: number } | null>(null);
  const aspect = measured?.tier === tier ? measured.ratio : heroVideoAspect(tier);
  const measure = (width: number, height: number) => {
    if (width > 0 && height > 0) setMeasured({ tier, ratio: width / height });
  };

  useEffect(() => {
    const pick = () => setTier(heroVideoTier(window.innerWidth));
    pick();
    window.addEventListener("resize", pick);
    return () => window.removeEventListener("resize", pick);
  }, []);

  const showVideo = !reduced && videoOk;

  // React does not emit the `muted` ATTRIBUTE in server-rendered markup, only
  // the property after hydration, so the browser's autoplay policy sees an
  // unmuted video while the page loads and refuses to start it. Mute through
  // the DOM and ask for playback explicitly, once per mounted element. A
  // refusal leaves the poster, which is the designed fallback.
  const videoRef = useRef<HTMLVideoElement | null>(null);
  useEffect(() => {
    const el = videoRef.current;
    if (!el || !showVideo) return;
    el.muted = true;
    el.defaultMuted = true;
    const attempt = el.play();
    if (attempt) attempt.catch(() => {});
  }, [showVideo, tier, theme]);

  return (
    <section id="top" className="relative pt-32 sm:pt-40">
      <div className="mx-auto max-w-[1180px] px-5 text-center sm:px-8">
        <h1 className="text-site-fg mx-auto max-w-4xl text-[2.5rem] leading-[1.04] font-semibold sm:text-6xl lg:text-[4.25rem]">
          Ask your papers.<br className="hidden sm:inline" /> Get answers{" "}
          <span className="text-site-accent whitespace-nowrap">with receipts.</span>
        </h1>
        <p className="text-site-muted mx-auto mt-6 max-w-2xl text-[1.0625rem] leading-relaxed sm:text-xl">
          Upload the papers you are reading and ask across them. Every sentence of the
          answer cites the passage it came from.
        </p>
        <div className="mt-9 flex flex-col items-stretch justify-center gap-3 sm:flex-row sm:items-center">
          <a
            href={routes.login()}
            className="bg-site-accent text-site-accent-fg inline-flex h-12 items-center justify-center gap-2 rounded-lg px-6 text-[15px] font-medium transition-[filter] hover:brightness-110"
          >
            Login
            <ArrowRight className="size-4" aria-hidden />
          </a>
          <a
            href="#how-it-works"
            onClick={(e) => {
              if (scrollToHash("#how-it-works", !reduced)) e.preventDefault();
            }}
            className="border-site-line text-site-fg hover:bg-site-panel inline-flex h-12 items-center justify-center rounded-lg border px-6 text-[15px] font-medium transition-colors"
          >
            See how it works
          </a>
        </div>
        <p className="text-site-muted mt-5 text-[13px]">
          Open source · Upload PDFs, ask across them, cite every sentence
        </p>
      </div>

      <figure className="mx-auto mt-14 max-w-[1180px] px-3 sm:mt-20 sm:px-8">
        <div className="border-site-line bg-site-panel shot-shadow overflow-hidden rounded-xl border">
          <div className="border-site-line flex h-10 items-center gap-3 border-b px-4">
            <span className="flex gap-1.5" aria-hidden>
              <span className="bg-site-line size-2.5 rounded-full" />
              <span className="bg-site-line size-2.5 rounded-full" />
              <span className="bg-site-line size-2.5 rounded-full" />
            </span>
            <span className="text-site-muted mx-auto truncate text-[12px]">
              ResearcherX · UAV Swarm Search
            </span>
            <span className="w-[42px]" aria-hidden />
          </div>
          {/* Portrait below 768px, where the phone recording plays. Contain,
              not cover: if the shape is ever off, the clip letterboxes
              rather than losing its edges. */}
          <div className="bg-site-bg relative" style={{ aspectRatio: aspect }}>
            {showVideo ? (
              // Remounted per theme and tier so the browser starts the new
              // file cleanly rather than seeking inside a half-buffered one.
              <video
                ref={videoRef}
                key={`${theme}-${tier}`}
                src={heroVideoSrc(theme, tier)}
                poster={heroPoster(theme, tier)}
                autoPlay
                muted
                loop
                playsInline
                preload="metadata"
                aria-label="ResearcherX answering questions about a drone-fleet paper library: an answer with citations, a refusal when the papers do not cover the question, and a comparison scoped to two papers"
                onLoadedMetadata={(e) => measure(e.currentTarget.videoWidth, e.currentTarget.videoHeight)}
                onError={() => setVideoOk(false)}
                className="absolute inset-0 h-full w-full object-contain"
              />
            ) : (
              // A static poster frame for reduced motion; next/image adds nothing.
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={heroPoster(theme, tier)}
                alt="ResearcherX answering a question about drone-swarm papers with numbered citations, one citation opened to its source passage"
                onLoad={(e) => measure(e.currentTarget.naturalWidth, e.currentTarget.naturalHeight)}
                className="absolute inset-0 h-full w-full object-contain"
              />
            )}
          </div>
        </div>
        <figcaption className="text-site-muted mt-4 text-center text-[13px]">
          Recorded in the real app: a cited answer with one citation opened, a question the
          papers do not cover refused, and a comparison scoped to two papers.
        </figcaption>
      </figure>
    </section>
  );
}
