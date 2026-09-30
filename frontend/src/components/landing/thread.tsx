"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { FileText, CornerDownRight } from "lucide-react";
import {
  DEMO_ANSWER,
  DEMO_CITATIONS,
  DEMO_PAPERS,
  DEMO_QUESTION,
  FOLLOWED_PAPER,
  REFUSAL,
  paperFor,
  type DemoCitation,
} from "@/lib/landing-demo";

/**
 * One question followed from the library to the manuscript, in four steps.
 * The same paper is tinted in every step (`.trace`), and each step pulses it
 * once as it scrolls in -- the page's single authored motion.
 */
export function Thread() {
  return (
    <section id="how-it-works" className="scroll-mt-20 py-24 sm:py-32">
      <div className="mx-auto max-w-[1180px] px-5 sm:px-8">
        <div className="max-w-2xl">
          <h2 className="text-site-fg text-3xl font-semibold sm:text-[2.75rem] sm:leading-[1.1]">
            Follow one question from library to cited answer.
          </h2>
          <p className="text-site-muted mt-5 text-[17px] leading-relaxed">
            The papers in this example are illustrative. Watch the highlighted one: it is the
            same source at every step.
          </p>
        </div>

        <ol className="mt-16 space-y-20 sm:mt-20 sm:space-y-28">
          <Step
            n={1}
            title="Build the library"
            body="Drop in PDFs. Each paper is split along its own sections and indexed, so you can ask about it as soon as it lands."
          >
            <LibraryVisual />
          </Step>
          <Step
            n={2}
            title="Ask across your papers"
            body="Ask in plain language, or type @ to limit a question to the papers you name. Every sentence of the answer carries a marker for the passage it rests on, and when the papers are silent, it says so instead of guessing."
          >
            <AnswerVisual />
          </Step>
          <Step
            n={3}
            title="Check the source"
            body="Open any marker to read the exact passage, with its section and page, before you rely on it. Nothing is paraphrased from memory."
          >
            <PassageVisual />
          </Step>
        </ol>
      </div>
    </section>
  );
}

function Step({
  n,
  title,
  body,
  id,
  children,
}: {
  n: number;
  title: string;
  body: string;
  id?: string;
  children: ReactNode;
}) {
  const ref = useRef<HTMLLIElement | null>(null);
  const [lit, setLit] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el || lit) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setLit(true);
          io.disconnect();
        }
      },
      { threshold: 0.45 },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [lit]);

  return (
    <li
      ref={ref}
      id={id}
      data-lit={lit}
      className="grid scroll-mt-24 grid-cols-[minmax(0,1fr)] gap-8 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)] lg:items-center lg:gap-16"
    >
      <div className="max-w-md">
        <span
          className="border-site-line text-site-muted inline-flex size-8 items-center justify-center rounded-full border text-[13px] font-medium tabular-nums"
          aria-hidden
        >
          {n}
        </span>
        <h3 className="text-site-fg mt-5 text-2xl font-semibold sm:text-[1.75rem]">
          <span className="sr-only">Step {n}: </span>
          {title}
        </h3>
        <p className="text-site-muted mt-4 text-[16px] leading-relaxed">{body}</p>
      </div>
      <div className="border-site-line bg-site-bg shot-shadow overflow-hidden rounded-xl border">
        {children}
      </div>
    </li>
  );
}

/* ── step 1 ─────────────────────────────────────────────────────────────── */

function LibraryVisual() {
  return (
    <div className="text-[13px]">
      <div className="border-site-line text-site-muted flex items-center justify-between border-b px-4 py-3">
        <span className="text-site-fg font-medium">Library</span>
        <span className="tabular-nums">{DEMO_PAPERS.length} papers</span>
      </div>
      <ul>
        {DEMO_PAPERS.map((p) => {
          const followed = p.id === FOLLOWED_PAPER.id;
          return (
            <li
              key={p.id}
              className={`border-site-line flex items-center gap-3 border-b px-4 py-3 last:border-b-0 ${
                followed ? "trace bg-site-accent-soft" : ""
              }`}
            >
              <FileText className="text-site-muted size-4 shrink-0" aria-hidden />
              <span className="text-site-fg min-w-0 flex-1 leading-snug font-medium">{p.title}</span>
              <span className="text-site-muted hidden shrink-0 sm:inline">{p.source}</span>
              <span className="text-site-muted inline-flex shrink-0 items-center gap-1.5">
                <span className="size-1.5 rounded-full bg-emerald-500" aria-hidden />
                Searchable
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/* ── step 2 ─────────────────────────────────────────────────────────────── */

function AnswerVisual() {
  const [openN, setOpenN] = useState<number | null>(null);
  return (
    <div className="space-y-5 p-4 sm:p-6">
      <UserBubble>{DEMO_QUESTION}</UserBubble>
      <p className="text-site-fg text-[15px] leading-[1.8]">
        {DEMO_ANSWER.map((s) => {
          const citation = DEMO_CITATIONS.find((c) => c.n === s.cite)!;
          return (
            <span key={s.cite}>
              {s.text}
              <Chip
                citation={citation}
                open={openN === citation.n}
                onOpenChange={(open) => setOpenN(open ? citation.n : null)}
              />{" "}
            </span>
          );
        })}
      </p>
      <p className="text-site-muted text-[13px]">Tap or point at a marker to read its passage.</p>
      <div className="border-site-line border-t pt-4">
        <p className="text-site-muted text-[12px] font-medium">Sources</p>
        <ol className="mt-2 space-y-1.5 text-[13px]">
          {DEMO_CITATIONS.map((c) => {
            const paper = paperFor(c);
            return (
              <li key={c.n} className="flex gap-2">
                <span className="text-site-muted tabular-nums">{c.n}.</span>
                <span
                  className={`text-site-fg rounded px-1 ${
                    paper.id === FOLLOWED_PAPER.id ? "trace bg-site-accent-soft" : ""
                  }`}
                >
                  {paper.title}
                </span>
              </li>
            );
          })}
        </ol>
      </div>
      <div className="border-site-line space-y-3 border-t pt-5">
        <UserBubble>Do they report energy use for fleets above sixteen drones?</UserBubble>
        <p className="text-site-muted text-[15px]">{REFUSAL}</p>
      </div>
    </div>
  );
}

function UserBubble({ children }: { children: ReactNode }) {
  return (
    <div className="flex justify-end">
      <p className="bg-site-accent text-site-accent-fg max-w-[85%] rounded-2xl rounded-br-md px-4 py-2.5 text-[14px] leading-snug">
        {children}
      </p>
    </div>
  );
}

/**
 * A citation marker. A mouse opens it on hover; touch and keyboard open it
 * with a tap or Enter, and it closes on a second tap, Escape, or a tap
 * elsewhere. Hover is mouse-only on purpose: on touch the pointer events that
 * precede a click would open it and the click would close it again.
 */
function Chip({
  citation,
  open,
  onOpenChange,
}: {
  citation: DemoCitation;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const wrap = useRef<HTMLSpanElement | null>(null);
  const paper = paperFor(citation);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (!wrap.current?.contains(e.target as Node)) onOpenChange(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onOpenChange(false);
    };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, onOpenChange]);

  return (
    <span ref={wrap} className="relative inline-block align-baseline">
      <button
        type="button"
        aria-expanded={open}
        aria-label={`Citation ${citation.n}, ${paper.title}`}
        onPointerEnter={(e) => e.pointerType === "mouse" && onOpenChange(true)}
        onPointerLeave={(e) => e.pointerType === "mouse" && onOpenChange(false)}
        onClick={() => onOpenChange(!open)}
        className={`relative ml-1 inline-flex h-[18px] min-w-[18px] -translate-y-[3px] items-center justify-center rounded px-1 text-[11px] font-semibold tabular-nums transition-colors before:absolute before:-inset-3 before:content-[''] ${
          open
            ? "bg-site-accent text-site-accent-fg"
            : "bg-site-accent-soft text-site-accent hover:bg-site-accent hover:text-site-accent-fg"
        }`}
      >
        {citation.n}
      </button>
      {open && (
        <span
          role="tooltip"
          className="border-site-line bg-site-bg shot-shadow absolute bottom-full left-1/2 z-30 mb-3 block w-[min(20rem,80vw)] -translate-x-1/2 rounded-lg border p-4 text-left sm:w-[22rem]"
        >
          <span className="text-site-fg block text-[13px] leading-snug font-semibold">
            {paper.title}
          </span>
          <span className="text-site-muted mt-1 block text-[12px]">{citation.locator}</span>
          <span className="text-site-fg mt-3 block text-[13px] leading-relaxed">
            {citation.passage}
          </span>
        </span>
      )}
    </span>
  );
}

/* ── step 3 ─────────────────────────────────────────────────────────────── */

function PassageVisual() {
  const c = DEMO_CITATIONS[0];
  const paper = paperFor(c);
  return (
    <div className="p-4 sm:p-6">
      <p className="text-site-muted flex items-center gap-2 text-[12px]">
        <CornerDownRight className="size-3.5" aria-hidden />
        Opened from marker {c.n}
      </p>
      <p className="mt-3">
        <span className="trace bg-site-accent-soft text-site-fg rounded px-1 text-[15px] font-semibold">
          {paper.title}
        </span>
      </p>
      <p className="text-site-muted mt-1.5 text-[13px]">{c.locator}</p>
      <blockquote className="border-site-line text-site-fg mt-5 border-l pl-4 text-[15px] leading-[1.8]">
        {c.passage}
      </blockquote>
    </div>
  );
}
