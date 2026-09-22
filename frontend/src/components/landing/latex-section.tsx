"use client";

import { useState } from "react";
import { Reveal } from "./reveal";

type Line = { n: number; code: string; block?: string };

const LINES: Line[] = [
  { n: 1, code: "\\section{Reward design}", block: "heading" },
  { n: 2, code: "\\label{sec:reward}", block: "heading" },
  { n: 3, code: "" },
  {
    n: 4,
    code: "The planner trades covered area against energy,",
    block: "para",
  },
  { n: 5, code: "penalizing close pairs \\cite{survey2024}.", block: "para" },
  { n: 6, code: "" },
  { n: 7, code: "\\begin{equation}", block: "equation" },
  { n: 8, code: "  R = \\alpha C - \\beta E - \\gamma P", block: "equation" },
  { n: 9, code: "\\end{equation}", block: "equation" },
  { n: 10, code: "" },
  { n: 11, code: "\\input{sections/experiments}", block: "input" },
  { n: 12, code: "\\bibliography{refs}", block: "input" },
];

export function LatexSection() {
  const [active, setActive] = useState<string | null>(null);

  const blockClass = (name: string) =>
    active === name ? "bg-lp-ink/10" : "bg-transparent";

  return (
    <section id="latex" className="bg-lp-ink text-lp-paper scroll-mt-16">
      <div className="mx-auto max-w-[1240px] px-5 py-24 sm:px-8 sm:py-32">
        <div className="grid gap-14 lg:grid-cols-[1fr_1.1fr] lg:items-start lg:gap-16">
          <Reveal>
            <p className="label-xs text-lp-paper/50">LaTeX</p>
            <h2 className="mt-5 text-3xl font-semibold sm:text-5xl">
              Write it up in the same place
            </h2>
            <p className="text-lp-paper/70 mt-6 max-w-md text-[15px] leading-relaxed">
              Multi-file projects with a real editor. Import a zip from Overleaf or
              arXiv and keep your structure. Compile with pdflatex or xelatex inside
              an isolated sandbox that holds no secrets.
            </p>
            <p className="text-lp-paper/70 mt-4 max-w-md text-[15px] leading-relaxed">
              SyncTeX works both ways: click a line of source to find it in the PDF,
              click the PDF to jump back to the line.
            </p>
            <p className="text-lp-paper/45 mt-6 font-mono text-[11px]">
              Hover a line or a block below to see the mapping.
            </p>
          </Reveal>

          <Reveal delay={120} className="grid gap-5 md:grid-cols-2">
            {/* Editor */}
            <div className="border-lp-paper/25 border">
              <div className="border-lp-paper/20 text-lp-paper/50 label-xs border-b px-4 py-3">
                main.tex
              </div>
              <ul className="py-3 font-mono text-[12px] leading-[1.9]">
                {LINES.map((l) => (
                  <li key={l.n}>
                    <button
                      type="button"
                      onMouseEnter={() => setActive(l.block ?? null)}
                      onMouseLeave={() => setActive(null)}
                      onFocus={() => setActive(l.block ?? null)}
                      onBlur={() => setActive(null)}
                      onClick={() => setActive(l.block ?? null)}
                      className={`flex w-full gap-4 px-4 text-left transition-colors ${
                        l.block && active === l.block
                          ? "bg-lp-paper/15"
                          : "hover:bg-lp-paper/10"
                      }`}
                    >
                      <span className="text-lp-paper/30 w-5 shrink-0 text-right">
                        {l.n}
                      </span>
                      <span className="text-lp-paper/85 break-all whitespace-pre-wrap">
                        {l.code || "\u00A0"}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>

            {/* Rendered page */}
            <div className="bg-lp-paper text-lp-ink border-lp-paper/25 border p-6">
              <p className="text-lp-gray-3 label-xs">main.pdf · page 5</p>

              <div
                onMouseEnter={() => setActive("heading")}
                onMouseLeave={() => setActive(null)}
                className={`mt-5 px-2 py-1 transition-colors ${blockClass("heading")}`}
              >
                <h3 className="font-serif text-xl">3.2 Reward design</h3>
              </div>

              <div
                onMouseEnter={() => setActive("para")}
                onMouseLeave={() => setActive(null)}
                className={`mt-2 px-2 py-1 transition-colors ${blockClass("para")}`}
              >
                <p className="font-serif text-[14px] leading-relaxed">
                  The planner trades covered area against energy, penalizing close
                  pairs [1].
                </p>
              </div>

              <div
                onMouseEnter={() => setActive("equation")}
                onMouseLeave={() => setActive(null)}
                className={`mt-3 px-2 py-3 text-center transition-colors ${blockClass("equation")}`}
              >
                <span className="font-serif text-[15px] italic">
                  R = αC − βE − γP
                </span>
              </div>

              <div
                onMouseEnter={() => setActive("input")}
                onMouseLeave={() => setActive(null)}
                className={`mt-3 px-2 py-1 transition-colors ${blockClass("input")}`}
              >
                <p className="text-lp-gray-3 font-serif text-[13px]">
                  4 Experiments · References
                </p>
              </div>
            </div>
          </Reveal>
        </div>
      </div>
    </section>
  );
}
