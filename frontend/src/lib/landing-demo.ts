/**
 * The example the landing page follows from library to manuscript.
 *
 * Every paper here is FICTIONAL, and the page labels it so: nothing is
 * attributed to a real publication. One paper -- FOLLOWED_PAPER -- appears in
 * all four steps (library row, answer citation, opened passage, \cite in the
 * LaTeX source), which is the thread the page is built on.
 *
 * Locators go through the app's own formatter, and the refusal is the chat's
 * fixed sentence, so the demo reads exactly the way the product does.
 */
import { formatChunkLocator } from "./citation-locator";

export type DemoPaper = {
  id: number;
  title: string;
  source: "PDF" | "arXiv" | "Manual";
  bibkey: string;
};

export const DEMO_PAPERS: DemoPaper[] = [
  {
    id: 1,
    title: "Decentralized Path Planning for Multi-Drone Survey Fleets",
    source: "PDF",
    bibkey: "ferrer2024",
  },
  {
    id: 2,
    title: "Collision-Aware Coordination Under Intermittent Links",
    source: "arXiv",
    bibkey: "nowak2023",
  },
  {
    id: 3,
    title: "Benchmarking Aerial Coverage Policies on Urban Grids",
    source: "PDF",
    bibkey: "raman2025",
  },
  {
    id: 4,
    title: "Energy-Aware Relay Placement for Drone Swarms",
    source: "arXiv",
    bibkey: "okafor2024",
  },
];

export const FOLLOWED_PAPER = DEMO_PAPERS[0];

export type DemoCitation = {
  n: number;
  paperId: number;
  locator: string;
  passage: string;
};

export const DEMO_CITATIONS: DemoCitation[] = [
  {
    n: 1,
    paperId: 1,
    locator: formatChunkLocator(["3 Method", "3.2 Reward design"], 5),
    passage:
      "The planner maximizes covered area per unit of energy while penalizing pairwise proximity below 4 m, so the reward is a weighted sum of coverage gain, battery cost and a separation penalty.",
  },
  {
    n: 2,
    paperId: 2,
    locator: formatChunkLocator(["4 Coordination", "4.1 Failure handling"], 8),
    passage:
      "When a link drops for more than two control cycles, each drone falls back to its last agreed corridor assignment rather than replanning globally, which bounds the worst-case detour at 12%.",
  },
  {
    n: 3,
    paperId: 3,
    locator: formatChunkLocator(["5 Evaluation", "5.3 Results"], 11),
    passage:
      "Across the twelve urban grids, the learned policy reaches 0.81 normalized coverage against 0.74 for the lawnmower baseline, with the gap widening as fleet size grows past eight drones.",
  },
];

export const DEMO_QUESTION = "How do these papers coordinate a drone fleet, and how well does it work?";

export const DEMO_ANSWER: { text: string; cite: number }[] = [
  {
    text: "The planner optimizes a weighted reward that trades covered area against battery use, with an explicit penalty for drones flying too close together.",
    cite: 1,
  },
  {
    text: "Coordination is decentralized: when a link drops, each drone keeps its last agreed corridor instead of replanning the whole fleet.",
    cite: 2,
  },
  {
    text: "On urban grid benchmarks this reaches 0.81 normalized coverage, against 0.74 for the lawnmower baseline.",
    cite: 3,
  },
];

/** The chat's fixed refusal, verbatim (backend/app/agents/chat_agent.py). */
export const REFUSAL = "The ingested documents do not cover this.";

export function paperFor(citation: DemoCitation): DemoPaper {
  const paper = DEMO_PAPERS.find((p) => p.id === citation.paperId);
  if (!paper) throw new Error(`demo citation ${citation.n} names no paper`);
  return paper;
}
