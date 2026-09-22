/**
 * The Graph screen's corpus — SAMPLE DATA FOR A DESIGN PREVIEW.
 *
 * There is no similarity backend. Nothing here calls one, stubs one at the
 * network layer, or pretends to. The papers, the distances, the facets and
 * every line of copy below are the approved "Reading Room" concept's own
 * content, kept verbatim so this screen stays consistent with the mockups it
 * came from.
 *
 * IT IS DELIBERATELY NOT THE PROJECT'S OWN LIBRARY. Drawing these distances
 * between the user's real paper titles would put invented measurements about
 * their real data on screen, which is the one thing this design has refused
 * throughout — a number on screen must be a number something produced. The
 * concept says as much where it explains why it shows four papers and not six:
 * "Showing six would mean inventing two."
 *
 * When a real similarity service exists, this module is the seam: the shapes
 * below are what the components consume.
 */

/** A paper that can be placed on the canvas. */
export type GraphPaper = {
  id: string;
  /** The node label. */
  short: string;
  /** The full title, used in the detail panel and as the node's tooltip. */
  full: string;
  byline: string;
  chunks: number;
  /**
   * Where this paper lands when it is added, in percent of the canvas box.
   * A HOME position, not a simulation seed: adding places the node here,
   * removing frees the spot, and nothing else on the canvas moves. There is no
   * physics in this screen and nothing settles.
   */
  home: { x: number; y: number };
};

/** A paper the picker offers and refuses, with the real reason. */
export type UnavailablePaper = { title: string; why: string };

/** An edge. `distance` is a cosine distance: smaller is nearer. */
export type GraphEdge = {
  a: string;
  b: string;
  distance: number;
  facet: string;
  /** What else the pair shares, or that this is their only link. */
  also: string;
  /** The two papers' claims on the shared facet. */
  claimA: string;
  claimB: string;
  /** What separates them anyway. */
  separates: string;
};

export const GRAPH_PAPERS: readonly GraphPaper[] = [
  {
    id: "p1",
    short: "Cooperative Multi-Target Search",
    full: "Cooperative Multi-Target Search with UAV Swarms",
    byline: "Yanmaz & Kandemir, 2021",
    chunks: 67,
    home: { x: 21, y: 24 },
  },
  {
    id: "p2",
    short: "Decentralised Task Reallocation",
    full: "Decentralised Task Reallocation under Agent Failure in Aerial Teams",
    byline: "Güven & Okumuş, 2023",
    chunks: 108,
    home: { x: 67, y: 70 },
  },
  {
    id: "p3",
    short: "Hybrid Split-Federated Learning",
    full: "Hybrid Split-Federated Learning for Bandwidth-Constrained Edge Fleets",
    byline: "Nakamura & Adeyemi, 2024",
    chunks: 96,
    home: { x: 17, y: 72 },
  },
  {
    id: "p4",
    short: "Voronoi Partitioning",
    full: "Voronoi Partitioning for Persistent Area Coverage",
    byline: "Halvorsen & Ruiz, 2022",
    chunks: 141,
    home: { x: 63, y: 21 },
  },
];

export const GRAPH_UNAVAILABLE: readonly UnavailablePaper[] = [
  {
    title: "NeMo-Mobility: Trace-Driven Models for Low-Power IoT Fleets",
    why: "Still ingesting, 41%. With no embeddings it has no distance to anything, so it cannot be placed.",
  },
  {
    title: "Deep RL Subagent Decomposition for Multi-Robot Patrol",
    why: "No extractable text, so nothing was ever embedded. Upload a readable copy to place it here.",
  },
];

export const GRAPH_EDGES: readonly GraphEdge[] = [
  {
    a: "p1",
    b: "p4",
    distance: 0.59,
    facet: "setting",
    also: "Also share method at 0.62 and problem at 0.68 — the only pair here linked on three facets.",
    claimA: "Six quadrotors, broadcast over 802.11, single operator.",
    claimB: "Up to 30 fixed-wing vehicles, 3 s beacon, no operator in the loop.",
    separates:
      "The nearest pair in your library — and they are nearest because they describe similar fleets in similar words, not because they argue the same thing.",
  },
  {
    a: "p1",
    b: "p2",
    distance: 0.74,
    facet: "problem",
    also: "Their only link above the cut.",
    claimA: "Keep a multi-target search covered when a vehicle stops reporting.",
    claimB: "Keep tasks moving when an agent stops bidding for them.",
    separates:
      "Same problem, incompatible units: one measures area held, the other tasks completed. That is why 0.79 and 0.91 are not comparable figures.",
  },
  {
    a: "p2",
    b: "p4",
    distance: 0.7,
    facet: "evidence",
    also: "Also share problem at 0.72.",
    claimA: "40 runs on a 12-robot ground testbed; agents removed by switching them off.",
    claimB: "30 outdoor flights; a vehicle commanded to land mid-mission on 11 of them.",
    separates:
      "Both use real hardware. Only one is airborne when it loses a member, which is the distinction your last chat turn turned on.",
  },
  {
    a: "p1",
    b: "p3",
    distance: 0.66,
    facet: "evidence",
    also: "Their only link above the cut.",
    claimA: "Bernoulli link loss in the authors’ own simulator.",
    claimB: "Replayed fleet traces, no hardware in the loop.",
    separates:
      "The one thing Split-Federated Learning shares with anything in your library is how it was evaluated, not what it claims.",
  },
];

/** The cut. The same figure governs retrieval in Chat, which is why it is
 *  stated on the screen rather than hidden in a config. */
export const GRAPH_CUT = 0.75;

/** Screen copy, verbatim from the concept. */
export const GRAPH_COPY = {
  eyebrow: "Multi-UAV coordination",
  title: "A graph you built",
  meta: ["Curated by you, not laid out for you", "Nodes stay where you put them"],
  derived:
    "Edges come from similarity between the papers’ embeddings, and every edge carries its distance and the facet the two papers actually share. Two papers can sit close in that space and argue about different things, so read an edge as a place to look rather than as a finding. Smaller distances are nearer; nothing looser than 0.75 is drawn.",
  /* The claim the reader meets before any number: stated once, where the
     numbers are introduced, and not repeated on every row. */
  preview:
    "Design preview — ResearcherX has no similarity backend yet. The six papers, the distances and the facets below are sample data from the design concept, not your library.",
  emptyTitle: "An empty graph",
  emptyBody:
    "Add a paper from the list on the right and it lands on the canvas. Add a second and any link closer than 0.75 is drawn between them, labelled with the distance and what the two papers share. Drag a node and it stays where you put it.",
  thresholdHeading: "Threshold",
  thresholdValue: "Cut at 0.75",
  thresholdNote:
    "The same 0.75 governs retrieval in Chat, so an edge you can see here is a link a question can reach.",
  pickerHeading: "Add a paper",
} as const;
