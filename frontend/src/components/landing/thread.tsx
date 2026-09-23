"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { FileText, Link2, CornerDownRight } from "lucide-react";
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
            Follow one question from library to manuscript.
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
            body="Drop in PDFs, paste an arXiv link, or type a reference by hand. Each paper is split along its own sections and indexed, so you can ask about it as soon as it lands."
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
          <Step
            n={4}
            title="Write it up in the same project"
            body="Keep your manuscript next to its sources: a multi-file LaTeX editor, Overleaf zip import, and a compiler with click-through between source and PDF."
            id="writing"
          >
            <LatexVisual />
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
              {p.source === "arXiv" ? (
                <Link2 className="text-site-muted size-4 shrink-0" aria-hidden />
              ) : (
                <FileText className="text-site-muted size-4 shrink-0" aria-hidden />
              )}
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

/* ── step 4 ─────────────────────────────────────────────────────────────── */

type Line = { n: number; code: ReactNode; block?: string };

const LINES: Line[] = [
  { n: 1, code: "\\subsection{Reward design}", block: "heading" },
  { n: 2, code: "" },
  { n: 3, code: "The planner trades coverage", block: "para" },
  { n: 4, code: "against energy, penalizing", block: "para" },
  {
    n: 5,
    code: (
      <>
        close pairs{" "}
        <span className="trace bg-site-accent-soft text-site-fg rounded px-0.5">
          \cite{"{"}
          {FOLLOWED_PAPER.bibkey}
          {"}"}
        </span>
        .
      </>
    ),
    block: "para",
  },
  { n: 6, code: "" },
  { n: 7, code: "\\begin{equation}", block: "equation" },
  { n: 8, code: "  R = \\alpha C - \\beta E", block: "equation" },
  { n: 9, code: "      - \\gamma P", block: "equation" },
  { n: 10, code: "\\end{equation}", block: "equation" },
];

function LatexVisual() {
  const [active, setActive] = useState<string | null>(null);
  const tint = (name: string) => (active === name ? "bg-site-accent-soft" : "");

  return (
    <div className="grid md:grid-cols-2">
      <div className="border-site-line border-b md:border-r md:border-b-0">
        <div className="border-site-line text-site-muted border-b px-4 py-2.5 text-[12px]">
          sections/method.tex
        </div>
        <ul className="py-2 font-mono text-[12px] leading-[1.9]">
          {LINES.map((l) => (
            <li key={l.n}>
              <button
                type="button"
                onPointerEnter={(e) => e.pointerType === "mouse" && setActive(l.block ?? null)}
                onPointerLeave={(e) => e.pointerType === "mouse" && setActive(null)}
                onFocus={() => setActive(l.block ?? null)}
                onBlur={() => setActive(null)}
                onClick={() => setActive(l.block ?? null)}
                className={`flex w-full gap-3 px-4 text-left transition-colors ${
                  l.block ? tint(l.block) : ""
                }`}
              >
                <span className="text-site-muted/60 w-4 shrink-0 text-right tabular-nums">
                  {l.n}
                </span>
                <span className="text-site-fg break-words whitespace-pre-wrap">
                  {l.code || " "}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>

      <div className="bg-white p-5 text-neutral-900">
        <p className="text-[11px] text-neutral-500">main.pdf · page 5</p>
        <div className={`mt-4 rounded px-2 py-1 transition-colors ${tint("heading")}`}>
          <p className="font-serif text-[15px] font-semibold">3.2 Reward design</p>
        </div>
        <div className={`mt-1 rounded px-2 py-1 transition-colors ${tint("para")}`}>
          <p className="font-serif text-[13.5px] leading-relaxed">
            The planner trades coverage against energy, penalizing close pairs [1].
          </p>
        </div>
        <div className={`mt-2 rounded px-2 py-2 text-center transition-colors ${tint("equation")}`}>
          <span className="font-serif text-[14px] italic">R = αC − βE − γP</span>
          <span className="float-right font-serif text-[12px] text-neutral-500">(3)</span>
        </div>
        <div className="mt-5 border-t border-neutral-200 pt-3">
          <p className="font-serif text-[11.5px] leading-relaxed text-neutral-600">
            [1] {FOLLOWED_PAPER.title}.
          </p>
        </div>
      </div>
      <p className="text-site-muted border-site-line col-span-full border-t px-4 py-2.5 text-[12px]">
        Point at a line of source, or tap it, to find it in the PDF.
      </p>
    </div>
  );
}
