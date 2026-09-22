/**
 * The Graph screen's corpus. It is SAMPLE DATA for a design preview.
 *
 * There is no similarity backend. Nothing here calls one, stubs one at the
 * network layer, or pretends to. The papers, the similarities, the facets and
 * the claims below come verbatim from the app prototype's mock data
 * (`src/lib/mock/graph.ts` and the Multi-UAV Coordination papers in
 * `src/lib/mock/papers.ts`, Lovable project 36777114), so this screen shows the
 * same nodes, links and rail as the prototype.
 *
 * It is NOT the project's own library. Drawing these similarities between the
 * user's real paper titles would put invented measurements about their data on
 * screen, and the preview note on the page says as much.
 *
 * When a real similarity service exists, this module is the seam: the shapes
 * below are what the components consume.
 */

/** A paper the rail can offer. The fields the Graph screen reads, and no more. */
export type GraphPaper = {
  id: string;
  title: string;
  authors: string[];
  year: number;
  /** Short topical tags, shown as badges in the detail panel. */
  facets: string[];
};

/** A paper placed on the canvas. `x`/`y` are in canvas units (900 x 520). */
export type GraphNode = {
  paperId: string;
  /** Short label: first author + year. */
  label: string;
  x: number;
  y: number;
};

export type GraphEdge = {
  id: string;
  /** paperId */
  a: string;
  /** paperId */
  b: string;
  similarity: number;
  sharedFacet: string;
  claimA: string;
  claimB: string;
  separation: string;
};

/** A paper the rail lists but refuses to place, with the reason. */
export type UnavailablePaper = {
  paperId: string;
  reason: string;
};

/** The sample library, in the prototype's order (the rail lists it in this order). */
export const GRAPH_PAPERS: readonly GraphPaper[] = [
  {
    id: "p-coverage",
    title: "Resilient Coverage Control for Heterogeneous Drone Teams",
    authors: ["L. Ferrer", "M. Oyelaran", "D. Strand"],
    year: 2024,
    facets: ["coverage recovery", "heterogeneous fleets", "Voronoi"],
  },
  {
    id: "p-bandwidth",
    title: "Bandwidth-Aware Consensus for Distributed Path Planning",
    authors: ["T. Nowak", "S. Beaumont"],
    year: 2023,
    facets: ["consensus", "bandwidth", "scheduling"],
  },
  {
    id: "p-reward",
    title: "Reward Shaping for Multi-Agent Patrol under Partial Observability",
    authors: ["P. Raman", "H. Adeyemi"],
    year: 2025,
    facets: ["reward design", "patrol", "partial observability"],
  },
  {
    id: "p-mobility",
    title: "Trace-Driven Mobility Models for Low-Power Aerial Fleets",
    authors: ["D. Strand", "K. Imaoka"],
    year: 2024,
    facets: ["mobility", "link uptime", "traces"],
  },
  {
    id: "p-formation",
    title: "Recovering Formation after Vehicle Loss: A Field Study",
    authors: ["L. Ferrer", "J. Okonkwo"],
    year: 2024,
    facets: ["coverage recovery", "field study", "formation"],
  },
  {
    id: "p-allocation",
    title: "Decentralised Task Allocation with Intermittent Links",
    authors: ["T. Nowak", "A. Villalba"],
    year: 2023,
    facets: ["task allocation", "auctions", "partitions"],
  },
  {
    id: "p-schedules",
    title: "Learning Communication Schedules for Swarm Relays",
    authors: ["S. Beaumont", "P. Raman"],
    year: 2025,
    facets: ["relays", "scheduling", "learning"],
  },
  {
    id: "p-fusion",
    title: "Sensor Fusion under Wind Disturbance for Quadrotor Teams",
    authors: ["K. Imaoka", "M. Oyelaran"],
    year: 2022,
    facets: ["sensor fusion", "wind", "estimation"],
  },
  {
    id: "p-benchmark",
    title: "A Benchmark for Coverage Loss Scenarios",
    authors: ["H. Adeyemi", "L. Ferrer", "T. Nowak"],
    year: 2025,
    facets: ["benchmark", "coverage recovery", "evaluation"],
  },
  {
    id: "p-hierarchical",
    title: "Hierarchical Planning for Search-and-Rescue Drones",
    authors: ["J. Okonkwo", "A. Villalba"],
    year: 2023,
    facets: ["hierarchical planning", "search and rescue"],
  },
  {
    id: "p-relay",
    title: "Energy-Aware Relay Placement for Aerial Meshes",
    authors: ["A. Villalba", "D. Strand"],
    year: 2024,
    facets: ["relays", "energy", "placement"],
  },
  {
    id: "p-safe",
    title: "Safe Exploration for Multi-Robot Mapping",
    authors: ["M. Oyelaran", "P. Raman"],
    year: 2025,
    facets: ["safe exploration", "mapping"],
  },
];

/** The papers on the canvas at first load, with their starting positions. */
export const GRAPH_NODES: readonly GraphNode[] = [
  { paperId: "p-coverage", label: "Ferrer 2024", x: 240, y: 150 },
  { paperId: "p-formation", label: "Ferrer 2024b", x: 520, y: 110 },
  { paperId: "p-bandwidth", label: "Nowak 2023", x: 330, y: 340 },
  { paperId: "p-reward", label: "Raman 2025", x: 640, y: 300 },
];

export const GRAPH_EDGES: readonly GraphEdge[] = [
  {
    id: "e1",
    a: "p-coverage",
    b: "p-formation",
    similarity: 0.82,
    sharedFacet: "coverage recovery",
    claimA: "Recovery time is dominated by traversal, not by re-partitioning.",
    claimB: "Reassignment latency explained under four seconds of a minute-long recovery.",
    separation:
      "One derives the bound analytically from the partition update; the other measures it in the field and never states a bound.",
  },
  {
    id: "e2",
    a: "p-coverage",
    b: "p-bandwidth",
    similarity: 0.67,
    sharedFacet: "distributed coordination",
    claimA: "The weighted partition converges within a bounded number of rounds.",
    claimB: "Budgeted gossip keeps the disagreement bound of full gossip.",
    separation:
      "They bound different quantities — area assignment versus state disagreement — and neither paper relates the two.",
  },
  {
    id: "e3",
    a: "p-formation",
    b: "p-bandwidth",
    similarity: 0.61,
    sharedFacet: "fleet coordination",
    claimA: "The inheriting vehicle's climb profile predicts the slow tail.",
    claimB: "A slower first round is the price of a scheduled link budget.",
    separation:
      "The field study treats communication as free; the consensus paper treats flight dynamics as absent.",
  },
  {
    id: "e4",
    a: "p-bandwidth",
    b: "p-reward",
    similarity: 0.72,
    sharedFacet: "partial information",
    claimA: "Agents act on a stale view when their slot has not come round.",
    claimB: "Patrol policies collapse when the observation is partial.",
    separation:
      "One staleness is imposed by the schedule and known; the other is a property of the environment and is not.",
  },
  {
    id: "e5",
    a: "p-coverage",
    b: "p-reward",
    similarity: 0.64,
    sharedFacet: "area persistence",
    claimA: "A residual hole can persist after the fleet stops moving.",
    claimB: "Staleness-keyed shaping stops the policy abandoning quiet regions.",
    separation:
      "The coverage paper treats persistence geometrically; the patrol paper treats it as a reward-design problem.",
  },
];

/** Papers the rail lists under "Not available", in this order. */
export const GRAPH_UNAVAILABLE: readonly UnavailablePaper[] = [
  { paperId: "p-fusion", reason: "no indexed text" },
  { paperId: "p-allocation", reason: "not checked yet" },
  { paperId: "p-hierarchical", reason: "added before the similarity index existed" },
  { paperId: "p-relay", reason: "not checked yet" },
];

/** Papers available to add to the canvas but not placed at first load. */
export const GRAPH_SPARE_NODES: readonly GraphNode[] = [
  { paperId: "p-benchmark", label: "Adeyemi 2025", x: 420, y: 230 },
  { paperId: "p-schedules", label: "Beaumont 2025", x: 180, y: 380 },
  { paperId: "p-safe", label: "Oyelaran 2025", x: 700, y: 180 },
  { paperId: "p-mobility", label: "Strand 2024", x: 560, y: 420 },
];
