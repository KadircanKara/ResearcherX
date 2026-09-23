"use client";

import { useEffect, useRef, useState } from "react";
import { routes } from "@/lib/routes";
import { usePrefersReducedMotion } from "@/hooks/use-reveal";
import { HERO_POSTER, HERO_VIDEO, heroVideoTier, type HeroVideoTier } from "@/lib/landing-video";
import { scrollToHash } from "./scroll-to";

const QUESTIONS = [
  "What reward function does the planner use?",
  "Compare the two federated approaches",
  "Which papers report results below the 0.75 threshold?",
];

function TypedQuestions({ enabled }: { enabled: boolean }) {
  const [index, setIndex] = useState(0);
  const [text, setText] = useState<string>(enabled ? "" : (QUESTIONS[0] ?? ""));

  useEffect(() => {
    if (!enabled) return;
    const full = QUESTIONS[index] ?? "";
    let timer: ReturnType<typeof setTimeout>;

    if (text.length < full.length) {
      timer = setTimeout(() => setText(full.slice(0, text.length + 1)), 42);
    } else {
      timer = setTimeout(() => {
        let i = full.length;
        const erase = setInterval(() => {
          i -= 1;
          setText(full.slice(0, Math.max(0, i)));
          if (i <= 0) {
            clearInterval(erase);
            setIndex((n) => (n + 1) % QUESTIONS.length);
          }
        }, 22);
      }, 3000);
    }
    return () => clearTimeout(timer);
  }, [text, index, enabled]);

  return (
    <p className="text-lp-paper/70 mt-8 font-mono text-[13px] sm:text-sm">
      <span className="text-lp-paper/40">Ask&nbsp;&nbsp;</span>
      <span className="text-lp-paper/85">{text}</span>
      {enabled && <span className="caret ml-0.5" aria-hidden="true" />}
    </p>
  );
}

export function Hero() {
  const reduced = usePrefersReducedMotion();
  const [videoOk, setVideoOk] = useState(true);
  // "md" on the server and for the first paint, so the markup the client
  // hydrates matches; the real tier lands in the effect below.
  const [tier, setTier] = useState<HeroVideoTier>("md");

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
  // refusal (a browser that blocks autoplay outright) leaves the black
  // fallback, which is the designed behaviour, so the rejection is ignored.
  const videoRef = useRef<HTMLVideoElement | null>(null);
  useEffect(() => {
    const el = videoRef.current;
    if (!el || !showVideo) return;
    el.muted = true;
    el.defaultMuted = true;
    const attempt = el.play();
    if (attempt) attempt.catch(() => {});
  }, [showVideo, tier]);

  return (
    <section
      id="top"
      className="bg-lp-ink relative flex min-h-[100svh] flex-col overflow-hidden"
    >
      <div className="absolute inset-0">
        {showVideo && (
          // Remounted per tier so the browser starts the new file cleanly
          // rather than seeking inside a half-buffered one.
          <video
            ref={videoRef}
            key={tier}
            src={HERO_VIDEO[tier]}
            poster={HERO_POSTER}
            autoPlay
            muted
            loop
            playsInline
            preload="metadata"
            aria-hidden="true"
            tabIndex={-1}
            onError={() => setVideoOk(false)}
            className="h-full w-full object-cover"
            style={{ filter: "grayscale(1) contrast(1.15)" }}
          />
        )}
        <div
          className="absolute inset-0"
          style={{
            background:
              "linear-gradient(to bottom, rgba(0,0,0,0.45) 0%, rgba(0,0,0,0.30) 45%, rgba(0,0,0,0.90) 100%)",
          }}
        />
        {/* Keep the left third darker on desktop so the copy stays readable */}
        <div
          className="absolute inset-0 hidden md:block"
          style={{
            background:
              "linear-gradient(to right, rgba(0,0,0,0.58) 0%, rgba(0,0,0,0.34) 33%, rgba(0,0,0,0) 62%)",
          }}
        />
        <div
          className="absolute inset-0"
          style={{
            background:
              "radial-gradient(ellipse at center, rgba(0,0,0,0) 40%, rgba(0,0,0,0.75) 100%)",
          }}
        />
        <div className="grain absolute inset-0" />
      </div>

      <div className="relative z-10 mx-auto flex w-full max-w-[1240px] flex-1 items-center px-5 pt-28 pb-24 sm:px-8">
        <div className="mx-auto max-w-2xl text-center md:mx-0 md:text-left">
          <p className="text-lp-paper/60 label-xs">A workspace for research papers</p>
          <h1 className="text-lp-paper mt-6 text-[2.6rem] leading-[0.98] font-semibold sm:text-6xl lg:text-[5.25rem]">
            Ask your papers.
            <br />
            Get answers{" "}
            <span className="serif-italic font-normal whitespace-nowrap">
              with receipts.
            </span>
          </h1>
          <p className="text-lp-paper/70 mx-auto mt-7 max-w-xl text-base leading-relaxed sm:text-lg md:mx-0">
            Upload the papers you are reading. Ask anything. Every sentence of the
            answer cites the section and page it came from.
          </p>
          <div className="mt-9 flex flex-col items-stretch gap-3 sm:flex-row sm:items-center sm:justify-center md:justify-start">
            <a
              href={routes.home()}
              className="pill-light px-7 py-3.5 text-sm font-medium"
            >
              Open the app
            </a>
            <a
              href="#how-it-works"
              onClick={(e) => {
                if (scrollToHash("#how-it-works", !reduced)) e.preventDefault();
              }}
              className="pill-ghost-light px-7 py-3.5 text-sm font-medium"
            >
              See how it works
            </a>
          </div>
          <TypedQuestions enabled={!reduced} />
        </div>
      </div>

      <div className="border-lp-paper/25 relative z-10 border-t">
        <div className="text-lp-paper/55 label-xs mx-auto flex max-w-[1240px] flex-wrap items-center justify-center gap-x-3 gap-y-2 px-5 py-4 sm:px-8 md:justify-start">
          <span>Section-level citations</span>
          <span aria-hidden="true">·</span>
          <span>Hybrid retrieval with reranking</span>
          <span aria-hidden="true">·</span>
          <span>Sandboxed LaTeX</span>
        </div>
      </div>
    </section>
  );
}
