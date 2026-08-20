/**
 * Explorer's corpus — STATIC DATA FOR A MOCK VIEW.
 *
 * There is no discovery backend. Nothing in Explorer calls one, stubs one at
 * the network layer, or pretends to. The papers, the distances and every line
 * of copy below are the approved "Reading Room" concept's own content, kept
 * verbatim so this screen stays consistent with the mockups it came from.
 *
 * When a real discovery service exists, this module is the seam: the shapes
 * below are what the components consume.
 */

/** The clock the mock is written against. Explicit, not `new Date()`: a mock
 *  whose "Today, 15:10" silently becomes "3 Aug 2026" next week is a mock that
 *  rots, and a live clock in a server-rendered component is a hydration
 *  mismatch waiting to happen. */
export const MOCK_NOW = "2026-08-19T16:40";

/** A run of evidence prose. `strong` marks a paper named inside it. */
export type EvidenceSpan = { text: string; strong?: boolean };

/** A term the candidate matched (`hit`) or did not (`miss`). */
export type Term = { term: string; kind: "hit" | "miss" };

export type Candidate = {
  id: string;
  title: string;
  byline: string;
  /** Distance to the library; `null` for a paper already in it. */
  distance: number | null;
  /** Already in the library — no action, and the row is subordinate. */
  held: boolean;
  /** The concept's second, argued-against candidate takes the ghost action. */
  action: "add" | "add-anyway" | null;
  evidence: EvidenceSpan[];
  terms: Term[];
};

export type Block =
  | { kind: "prose"; text: string }
  | { kind: "candidate"; candidate: Candidate };

export type Turn = {
  n: number;
  /** The user's own words. `mention` marks an `@`-named paper. */
  question: { text: string; mention?: boolean }[];
  blocks: Block[];
};

export type Exploration = {
  id: string;
  title: string;
  lastAsked: string;
  added: number;
  exchanges: number;
  considered: number;
  startedAt: string;
  lastActivityAt: string;
  turns: Turn[];
  scoredAgainst: { title: string; chunks: number }[];
  railNote: string;
  addedFrom: { title: string; distance: number; note: string }[];
};

const FIELD_EVIDENCE: Exploration = {
  id: "field-evidence-coverage-under-vehicle-loss",
  title: "Field evidence for coverage under vehicle loss",
  lastAsked: "Closer to Decentralised Task Reallocation — the market-based one.",
  added: 1,
  exchanges: 3,
  considered: 6,
  startedAt: "2026-08-19T11:20",
  lastActivityAt: "2026-08-19T15:10",
  turns: [
    {
      n: 1,
      question: [
        {
          text: "I have four papers on UAV coverage when a vehicle drops out mid-mission. What am I missing?",
        },
      ],
      blocks: [
        {
          kind: "prose",
          text: "Your library models that failure carefully and has watched it happen once. Cooperative Multi-Target Search and Voronoi Partitioning both define what recovery means, and Decentralised Task Reallocation gives you the market-based alternative — but only one of the four has ever lost a vehicle in the air. The gap is evidence, not theory.",
        },
        {
          kind: "prose",
          text: "One candidate fills exactly that. It cites both of your aerial papers and reports 40 flights with a vehicle cut mid-mission, measured from onboard logs.",
        },
        {
          kind: "candidate",
          candidate: {
            id: "x1",
            title: "Field Validation of Persistent Coverage under In-Flight Vehicle Loss",
            byline: "Halvorsen, M., Ruiz, P. & Sato, K. · 2026 · arXiv preprint",
            distance: 0.58,
            held: false,
            action: "add",
            evidence: [
              { text: "Cites " },
              { text: "Voronoi Partitioning", strong: true },
              { text: " (2022) and " },
              { text: "Cooperative Multi-Target Search", strong: true },
              {
                text: " (2021), both already in your library. Nearest to Voronoi Partitioning at 0.58; nearest chunk is its Section V, on recovery measured from flight logs rather than simulation.",
              },
            ],
            terms: [
              { term: "in-flight vehicle loss", kind: "hit" },
              { term: "coverage recovery", kind: "hit" },
              { term: "outdoor trial", kind: "hit" },
              { term: "auction reallocation", kind: "miss" },
            ],
          },
        },
        {
          kind: "prose",
          text: "A second one keeps coming up and I would leave it. A survey cites three of your four, which makes it look central, but it summarises work you already hold and adds no new evidence — it will pull retrieval toward restatements of your own library.",
        },
        {
          kind: "candidate",
          candidate: {
            id: "x2",
            title: "A Survey of Coverage Control for Multi-Robot Systems",
            byline: "Pereira, C., Aydın, T. & Novak, R. · 2024 · Annual Reviews in Control",
            distance: 0.69,
            held: false,
            action: "add-anyway",
            evidence: [
              {
                text: "Cites three of your four papers. Useful for framing a related-work section; for answering a question it overlaps everything you have. If you add it, expect it to appear in retrieval for almost any question about coverage.",
              },
            ],
            terms: [
              { term: "coverage control", kind: "hit" },
              { term: "multi-robot", kind: "hit" },
              { term: "agent failure", kind: "miss" },
              { term: "field trial", kind: "miss" },
            ],
          },
        },
      ],
    },
    {
      n: 2,
      question: [{ text: "Not surveys. Only work with real flights." }],
      blocks: [
        {
          kind: "prose",
          text: "That drops the survey and two others I had not put forward. One new candidate survives, and it is a partial fit rather than a clean one.",
        },
        {
          kind: "candidate",
          candidate: {
            id: "x3",
            title: "Fault-Tolerant Formation Flight with In-Air Vehicle Removal",
            byline: "Sørensen, K. & Ito, H. · 2025 · IROS",
            distance: 0.64,
            held: false,
            action: "add",
            evidence: [
              {
                text: "Flies, and removes a vehicle in the air — but it measures formation error, not area observed. It answers “did the fleet survive the loss”, which is next to, not the same as, “was the ground still being watched”.",
              },
            ],
            terms: [
              { term: "in-air removal", kind: "hit" },
              { term: "flight trial", kind: "hit" },
              { term: "fault tolerance", kind: "hit" },
              { term: "area coverage", kind: "miss" },
              { term: "staleness bound", kind: "miss" },
            ],
          },
        },
        {
          kind: "prose",
          text: "I also checked what you already hold against this request, because the closest match to it is not new. Voronoi Partitioning is already in your library and is the one paper of the four that flies the failure.",
        },
        {
          kind: "candidate",
          candidate: {
            id: "x4",
            title: "Voronoi Partitioning for Persistent Area Coverage",
            byline: "Halvorsen, M. & Ruiz, P. · 2022 · Journal of Field Robotics",
            distance: null,
            held: true,
            action: null,
            evidence: [
              {
                text: "Added 12 Aug, embedded with 141 chunks. Nothing to add — but it is worth knowing that the strongest answer to this question is already in the library rather than outside it.",
              },
            ],
            terms: [
              { term: "coverage guarantee", kind: "hit" },
              { term: "vehicle loss", kind: "hit" },
            ],
          },
        },
      ],
    },
    {
      n: 3,
      question: [
        { text: "Closer to " },
        { text: "@Decentralised Task Reallocation under Agent Failure", mention: true },
        { text: " — the market-based one." },
      ],
      blocks: [
        {
          kind: "prose",
          text: "Then the axis moves from coverage to assignment. Your reallocation paper is ground robots on a floor with a two-second auction round; the combination missing from your library is aerial and market-based at once, where radio loss and the auction interact.",
        },
        {
          kind: "candidate",
          candidate: {
            id: "x5",
            title: "Auction Protocols for Aerial Task Reallocation under Radio Loss",
            byline: "Okonkwo, A. & Lindqvist, J. · 2025 · IEEE Transactions on Robotics",
            distance: 0.63,
            held: false,
            action: "add",
            evidence: [
              { text: "Same problem as " },
              { text: "Decentralised Task Reallocation", strong: true },
              {
                text: ", airborne. Nearest to it at 0.63, which would make them the second-closest pair in your library after the two aerial coverage papers.",
              },
            ],
            terms: [
              { term: "task reallocation", kind: "hit" },
              { term: "radio loss", kind: "hit" },
              { term: "aerial fleet", kind: "hit" },
              { term: "coverage guarantee", kind: "miss" },
            ],
          },
        },
        {
          kind: "prose",
          text: "One caution before you add it: it shares both problem and method with the paper you just named, so it deepens the corner of your library that is already strongest. It does nothing for Hybrid Split-Federated Learning, which still connects to the rest of your corpus by a single 0.66 edge.",
        },
      ],
    },
  ],
  scoredAgainst: [
    { title: "Cooperative Multi-Target Search", chunks: 67 },
    { title: "Decentralised Task Reallocation", chunks: 108 },
    { title: "Hybrid Split-Federated Learning", chunks: 96 },
    { title: "Voronoi Partitioning", chunks: 141 },
  ],
  railNote:
    "Chunk counts. Two more papers sit in the library with no embeddings — one still ingesting, one with no extractable text — so they have no distance to anything and do not affect what comes back here.",
  addedFrom: [
    {
      title: "Field Validation of Persistent Coverage under In-Flight Vehicle Loss",
      distance: 0.58,
      note: "Ingesting now. It joins Papers, the Graph and your next question on its own.",
    },
  ],
};

/** The other four rows of the concept's list. The concept draws one thread in
 *  full and stubs the rest, so they carry their list metadata and no turns —
 *  opening one says so rather than inventing an exchange. */
function stub(
  e: Omit<Exploration, "turns" | "scoredAgainst" | "railNote" | "addedFrom">
): Exploration {
  return {
    ...e,
    turns: [],
    scoredAgainst: FIELD_EVIDENCE.scoredAgainst,
    railNote: FIELD_EVIDENCE.railNote,
    addedFrom: [],
  };
}

export const EXPLORATIONS: Exploration[] = [
  FIELD_EVIDENCE,
  stub({
    id: "market-based-reallocation-airborne",
    title: "Market-based reallocation, airborne",
    lastAsked: "Anything where the auction period and the radio timeout are tuned together?",
    added: 2,
    exchanges: 5,
    considered: 9,
    startedAt: "2026-08-18T09:40",
    lastActivityAt: "2026-08-18T10:22",
  }),
  stub({
    id: "split-learning-on-robot-fleets",
    title: "Split learning on robot fleets",
    lastAsked: "Is there anything that would connect Hybrid Split-Federated to the rest?",
    added: 0,
    exchanges: 2,
    considered: 5,
    startedAt: "2026-08-16T14:02",
    lastActivityAt: "2026-08-16T14:35",
  }),
  stub({
    id: "benchmarks-for-persistent-coverage",
    title: "Benchmarks for persistent coverage",
    lastAsked: "Which of these report a coverage figure I could compare against 0.79?",
    added: 3,
    exchanges: 7,
    considered: 14,
    startedAt: "2026-08-11T08:15",
    lastActivityAt: "2026-08-11T11:48",
  }),
  stub({
    id: "work-citing-yanmaz-2021",
    title: "Work citing Yanmaz 2021",
    lastAsked: "Only the ones that disagree with it.",
    added: 1,
    exchanges: 4,
    considered: 8,
    startedAt: "2026-08-02T16:30",
    lastActivityAt: "2026-08-02T17:05",
  }),
];

/** The empty state's three prompts, taken from the Graph in the concept. */
export const SUGGESTIONS: { prompt: string; why: string; exploration: string }[] = [
  {
    prompt: "field validation of swarm coverage under vehicle loss",
    why: "Only one of your four papers has flown the failure it models.",
    exploration: FIELD_EVIDENCE.id,
  },
  {
    prompt: "auction-based reallocation for aerial fleets",
    why: "Two papers meet at this idea, but neither is aerial and market-based at once.",
    exploration: "market-based-reallocation-airborne",
  },
  {
    prompt: "split learning on mobile robot fleets",
    why: "Hybrid Split-Federated Learning hangs on a single 0.66 edge. This would give it neighbours.",
    exploration: "split-learning-on-robot-fleets",
  },
];

export const EMPTY_META = "Nothing explored yet · scored against 4 embedded papers";

export function findExploration(id: string): Exploration | undefined {
  return EXPLORATIONS.find((e) => e.id === id);
}
