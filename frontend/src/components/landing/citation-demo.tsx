"use client";

import { useState } from "react";
import { Reveal } from "./reveal";

type Citation = {
  id: number;
  paper: string;
  locator: string;
  passage: string;
};

// Placeholder papers, deliberately fictional: nothing here is attributed to a
// real publication.
const CITATIONS: Citation[] = [
  {
    id: 1,
    paper: "Decentralized Path Planning for Multi-Drone Survey Fleets",
    locator: "Section 3.2 > Reward design  ·  Page 5",
    passage:
      "The planner maximizes covered area per unit of energy while penalizing pairwise proximity below 4 m, so the reward is a weighted sum of coverage gain, battery cost and a separation penalty.",
  },
  {
    id: 2,
    paper: "Collision-Aware Coordination Under Intermittent Links",
    locator: "Section 4.1 > Failure handling  ·  Page 8",
    passage:
      "When a link drops for more than two control cycles, each drone falls back to its last agreed corridor assignment rather than replanning globally, which bounds the worst-case detour at 12%.",
  },
  {
    id: 3,
    paper: "Benchmarking Aerial Coverage Policies on Urban Grids",
    locator: "Section 5.3 > Results  ·  Page 11",
    passage:
      "Across the twelve urban grids, the learned policy reaches 0.81 normalized coverage against 0.74 for the lawnmower baseline, with the gap widening as fleet size grows past eight drones.",
  },
];

const SENTENCES = [
  {
    text: "The planner optimizes a weighted reward that trades covered area against battery use, with an explicit penalty for drones drifting too close to each other.",
    cite: 1,
  },
  {
    text: "Coordination is decentralized: when communication drops, each drone keeps its last agreed corridor instead of replanning the whole fleet.",
    cite: 2,
  },
  {
    text: "On urban grid benchmarks this reaches 0.81 normalized coverage, compared with 0.74 for the lawnmower baseline.",
    cite: 3,
  },
];

/* A refusal is not a claim drawn from a passage, so it carries no marker. */
const REFUSAL =
  "The ingested papers do not report energy figures for fleets larger than sixteen drones, so that case is not covered.";

function Chip({
  citation,
  open,
  onOpen,
  onClose,
}: {
  citation: Citation;
  open: boolean;
  onOpen: () => void;
  onClose: () => void;
}) {
  return (
    <span className="relative inline-block align-baseline">
      <button
        type="button"
        aria-expanded={open}
        onMouseEnter={onOpen}
        onMouseLeave={onClose}
        onFocus={onOpen}
        onBlur={onClose}
        onClick={() => (open ? onClose() : onOpen())}
        className={`border-lp-paper/50 ml-1 inline-flex h-5 min-w-5 items-center justify-center border px-1 font-mono text-[11px] leading-none transition-colors ${
          open ? "bg-lp-paper text-lp-ink" : "text-lp-paper/80 hover:bg-lp-paper hover:text-lp-ink"
        }`}
      >
        [{citation.id}]
      </button>

      {open && (
        <span
          role="tooltip"
          className="bg-lp-paper text-lp-ink border-lp-ink absolute bottom-full left-0 z-30 mb-2 block w-[min(20rem,78vw)] border p-4 text-left sm:w-[22rem]"
        >
          <span className="block text-[13px] font-semibold">{citation.paper}</span>
          <span className="text-lp-gray-4 mt-1.5 block font-mono text-[11px]">
            {citation.locator}
          </span>
          <span className="text-lp-gray-4 mt-3 block text-[13px] leading-relaxed">
            “{citation.passage}”
          </span>
        </span>
      )}
    </span>
  );
}

export function CitationDemo() {
  const [openId, setOpenId] = useState<number | null>(null);

  return (
    <section className="bg-lp-ink text-lp-paper">
      <div className="mx-auto max-w-[1240px] px-5 py-24 sm:px-8 sm:py-32">
        <Reveal>
          <p className="label-xs text-lp-paper/50">Answers with receipts</p>
          <h2 className="mt-5 max-w-2xl text-3xl font-semibold sm:text-5xl">
            Every sentence points somewhere.
          </h2>
        </Reveal>

        <Reveal delay={120} className="mt-14">
          <div className="border-lp-paper/25 mx-auto max-w-3xl border p-5 sm:p-8">
            <div className="border-lp-paper/30 inline-block max-w-full border px-4 py-3 text-[15px]">
              How do these papers coordinate a drone fleet, and how well does it do?
            </div>

            <p className="mt-8 text-[16px] leading-[1.85] sm:text-[17px]">
              {SENTENCES.map((s) => {
                const citation = CITATIONS.find((c) => c.id === s.cite)!;
                return (
                  <span key={s.text}>
                    {s.text}
                    <Chip
                      citation={citation}
                      open={openId === citation.id}
                      onOpen={() => setOpenId(citation.id)}
                      onClose={() => setOpenId(null)}
                    />{" "}
                  </span>
                );
              })}
              <span className="text-lp-paper/45">{REFUSAL}</span>
            </p>

            <div className="border-lp-paper/20 mt-10 border-t pt-6">
              <p className="label-xs text-lp-paper/45">Sources</p>
              <ol className="mt-4 space-y-2">
                {CITATIONS.map((c) => (
                  <li key={c.id} className="flex gap-3 text-[13px]">
                    <span className="text-lp-paper/50 font-mono">[{c.id}]</span>
                    <span className="text-lp-paper/75">{c.paper}</span>
                  </li>
                ))}
              </ol>
            </div>
          </div>

          <p className="text-lp-paper/45 mt-6 text-center text-[13px]">
            This is what every answer looks like. Nothing is said without a marker.
          </p>
        </Reveal>
      </div>
    </section>
  );
}
