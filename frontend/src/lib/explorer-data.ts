/**
 * Explorer's corpus — STATIC DATA FOR A MOCK VIEW.
 *
 * There is no discovery backend. Nothing in Explorer calls one, stubs one at
 * the network layer, or pretends to. The papers, the sources, the distances
 * and every line of copy below are the approved design's own content, kept
 * verbatim so this screen stays consistent with the prototype it came from.
 *
 * When a real discovery service exists, this module is the seam: the shapes
 * below are what the components consume.
 *
 * Dates are `YYYY-MM-DDTHH:MM` strings measured against `MOCK_NOW`, never
 * `new Date()`. Two reasons, both learned here: a mock whose "Today, 9:42 AM"
 * silently becomes a date three weeks stale is a mock that rots, and a live
 * clock in a server-rendered component is a hydration mismatch waiting to
 * happen. `lib/explorer.ts` turns a stamp into the text.
 */

/** The clock every stamp below is written against. */
export const MOCK_NOW = "2026-09-22T16:40";

export type Source = {
  id: number;
  title: string;
  domain: string;
  year: number;
  summary: string;
};

export type Candidate = {
  id: string;
  title: string;
  byline: string;
  /** Distance to the project library; absent for a paper already in it. */
  distance?: number;
  evidence: string;
  matched: string[];
  missed: string[];
  /**
   * `library` — already in the project, so there is nothing to add.
   * `against` — the exploration argues against adding it; the action stays
   * available ("Add anyway") because the judgement is the assistant's, not
   * the user's.
   */
  status?: "library" | "against";
};

export type ReviewIssue = {
  claim: string;
  severity: "low" | "medium" | "high";
  note: string;
};

export type SearchQuery = {
  query: string;
  status: "validated" | "retried once" | "kept, degraded";
  /** Set only for `retried once`: the query the retry actually ran. */
  revisedQuery?: string;
  sources: Source[];
};

export type ExplorationTurn = {
  question: string;
  rationale: string;
  queries: SearchQuery[];
  answer: string;
  sources: Source[];
  candidates: Candidate[];
  review: "pass" | ReviewIssue[];
  followUps: string[];
};

export type ExplorationThread = {
  id: string;
  title: string;
  project: string;
  /** `YYYY-MM-DDTHH:MM`, read against `MOCK_NOW`. */
  started: string;
  /** `YYYY-MM-DDTHH:MM`, read against `MOCK_NOW`. */
  activity: string;
  /** Papers the exploration weighed, including the ones it rejected. */
  considered: number;
  added: number;
  /** The one paper the thread starts out having added, before this session. */
  baselineAdded: string;
  turns: ExplorationTurn[];
};

/** The project library every candidate is scored against. */
export const libraryPapers = [
  { title: "Distributed Coverage with Sparse Fleet Telemetry", chunks: 18 },
  { title: "Resilient Task Allocation for Aerial Teams", chunks: 14 },
  { title: "Communication-Aware Swarm Replanning", chunks: 11 },
  { title: "Local Learning Across Mobile Robots", chunks: 9 },
];

/** The library-scope picker's options. */
export const projectScopes = [
  { value: "uav", label: "Multi-UAV Coordination" },
  { value: "fleet", label: "Fleet Intelligence" },
  { value: "resilient", label: "Resilient Autonomy" },
];

const source = (
  id: number,
  title: string,
  domain: string,
  year: number,
  summary: string
): Source => ({ id, title, domain, year, summary });

const coverageSources = [
  source(1, "Graceful Degradation in Cooperative Aerial Coverage", "aerial-systems.test", 2025, "Models local handoff policies after abrupt vehicle loss."),
  source(2, "Event-Triggered Replanning for Sparse Robot Networks", "robotics-index.test", 2024, "Limits communication while recovering coverage objectives."),
  source(3, "Topology-Aware Recovery for Mobile Sensor Teams", "open-research.test", 2026, "Links graph redundancy to post-failure recovery time."),
  source(4, "Local Cell Handoffs in Depleted Drone Teams", "autonomy-papers.test", 2025, "Compares neighbor-first assignment transfers after single and paired failures."),
  source(5, "Partition-Tolerant Coverage Repair", "field-robots.test", 2024, "Tests temporary duplicated coverage when fleet links are intermittent."),
  source(6, "Failure Detection Latency in Aerial Meshes", "swarm-systems.test", 2026, "Measures how heartbeat intervals affect the start of recovery."),
] as const;

const learningSources = [
  source(11, "Federated Updates Across Intermittent Robot Fleets", "fleet-learning.test", 2026, "Evaluates buffered model updates across recurring network partitions."),
  source(12, "Staleness-Aware Aggregation for Mobile Learners", "edge-robotics.test", 2025, "Weights delayed local models by age and route uncertainty."),
  source(13, "Peer-to-Peer Learning Under Fleet Churn", "distributed-ai.test", 2024, "Studies gossip aggregation as robots enter and leave a fleet."),
  source(14, "Energy Budgets for On-Robot Model Exchange", "robot-compute.test", 2025, "Profiles radio and compute costs for compressed model deltas."),
  source(15, "Drift Detection in Heterogeneous Robot Teams", "adaptive-systems.test", 2026, "Finds local drift tests useful before merging non-identical observations."),
  source(16, "Asynchronous Consensus for Fleet Intelligence", "mobile-ml.test", 2024, "Compares server-led and decentralized asynchronous learning rounds."),
] as const;

const planningSources = [
  source(21, "Bandwidth-Bounded Decentralized Path Negotiation", "motion-planning.test", 2026, "Uses compact intent envelopes instead of full trajectory exchange."),
  source(22, "Conflict Resolution with Sparse Neighbor Messages", "multi-robot.test", 2025, "Ranks local right-of-way rules under delayed neighbor updates."),
  source(23, "Event-Triggered Intent Sharing for Robot Teams", "planning-archive.test", 2024, "Sends trajectory changes only when collision risk crosses a threshold."),
  source(24, "Local Safe Corridors Under Packet Loss", "autonomous-motion.test", 2025, "Maintains conservative corridors during communication gaps."),
  source(25, "Priority Tokens for Congested Robot Networks", "networked-agents.test", 2026, "Encodes right-of-way with small transferable priority tokens."),
  source(26, "Receding-Horizon Plans with Delayed Peers", "robot-paths.test", 2024, "Bounds collision risk when peer plans arrive out of date."),
] as const;

const coverageCandidates: Candidate[] = [
  { id: "coverage-graceful", title: coverageSources[0].title, byline: "Mira Venn, Tao Ellery · Autonomous Systems Letters · 2025", distance: 0.31, evidence: "Directly extends the failure model in Distributed Coverage with Sparse Fleet Telemetry with a local responsibility-transfer rule.", matched: ["vehicle loss", "coverage", "multi-UAV"], missed: ["obstacles"] },
  { id: "coverage-topology", title: coverageSources[2].title, byline: "Ilya Noor, Sam Adey · Field Robotics Review · 2026", distance: 0.58, evidence: "Adds graph redundancy measures absent from Resilient Task Allocation for Aerial Teams, but evaluates mixed ground-air teams.", matched: ["recovery", "coordination"], missed: ["UAV-only"] },
  { id: "coverage-library", title: "Communication-Aware Swarm Replanning", byline: "Nadia Sol, Peter Wren · Networked Robotics · 2023", evidence: "Already in the project library and useful as the communication-cost baseline.", matched: ["replanning", "swarm"], missed: [], status: "library" },
  { id: "coverage-marine", title: "Resilient Sampling with Autonomous Marine Fleets", byline: "Evan Rowe, Li Chen · Oceanic Autonomy · 2024", distance: 0.82, evidence: "The failure response is relevant, but marine dynamics make transfer to aerial coverage uncertain.", matched: ["vehicle loss"], missed: ["aerial", "coverage"], status: "against" },
];

const learningCandidates: Candidate[] = [
  { id: "learning-stale", title: learningSources[1].title, byline: "Rhea Moss, Jun Vale · Mobile Intelligence · 2025", distance: 0.27, evidence: "Extends Local Learning Across Mobile Robots with explicit controls for delayed updates and route-dependent staleness.", matched: ["federated learning", "intermittent links"], missed: ["adversarial clients"] },
  { id: "learning-churn", title: learningSources[2].title, byline: "Omar Saye, Lin Park · Distributed Autonomy · 2024", distance: 0.49, evidence: "Adds fleet membership churn to the local-learning assumptions represented in the library.", matched: ["robot fleets", "peer learning"], missed: ["central server"] },
  { id: "learning-library", title: "Local Learning Across Mobile Robots", byline: "Ari Bell, Sora Finch · Fleet Computing · 2023", evidence: "Already in the project library and anchors the local-update comparison.", matched: ["local learning", "mobile robots"], missed: [], status: "library" },
  { id: "learning-satellite", title: "Federated Models over Orbital Relay Networks", byline: "Pia North, Dev Arin · Remote Systems · 2025", distance: 0.79, evidence: "Handles long disconnections, but the fixed orbital schedule differs from mobile robot contact patterns.", matched: ["federated", "disconnected"], missed: ["robot fleet"], status: "against" },
];

const planningCandidates: Candidate[] = [
  { id: "planning-bandwidth", title: planningSources[0].title, byline: "Elia Ford, Minh Aster · Robot Motion Notes · 2026", distance: 0.34, evidence: "Complements Communication-Aware Swarm Replanning with a compact representation of future motion intent.", matched: ["decentralized", "bandwidth", "path planning"], missed: ["vehicle loss"] },
  { id: "planning-corridors", title: planningSources[3].title, byline: "Noa Kim, Remy Holt · Safe Autonomy Review · 2025", distance: 0.55, evidence: "Connects sparse communication to conservative collision avoidance, adjacent to the library's replanning work.", matched: ["packet loss", "safe paths"], missed: ["coverage"] },
  { id: "planning-library", title: "Communication-Aware Swarm Replanning", byline: "Nadia Sol, Peter Wren · Networked Robotics · 2023", evidence: "Already in the project library and provides the closest communication baseline.", matched: ["replanning", "bandwidth"], missed: [], status: "library" },
  { id: "planning-warehouse", title: "Central Dispatch for Dense Warehouse Robots", byline: "Gita Ames, Bo Ren · Logistics Automation · 2024", distance: 0.76, evidence: "Reports strong throughput, but relies on a central dispatcher and reliable indoor networking.", matched: ["multi-robot paths"], missed: ["decentralized", "limited bandwidth"], status: "against" },
];

export const explorationThreads: ExplorationThread[] = [
  {
    id: "uav-loss",
    title: "Coverage after vehicle loss",
    project: "Multi-UAV Coordination",
    started: "2026-09-22T09:42",
    activity: "2026-09-22T16:28",
    considered: 14,
    added: 3,
    baselineAdded: "Failure-Aware Coverage Baselines",
    turns: [
      {
        question: "How do multi-UAV systems maintain coverage after vehicle loss?",
        rationale: "Separate failure detection, local handoff, and network-wide recovery so their evidence can be compared cleanly.",
        queries: [
          { query: "multi-UAV coverage recovery after vehicle failure", status: "validated", sources: coverageSources.slice(0, 4) },
          { query: "distributed drone task reallocation partial fleet loss", status: "retried once", revisedQuery: "topology-aware aerial coverage recovery", sources: coverageSources.slice(2, 6) },
          { query: "communication-efficient swarm replanning failures", status: "validated", sources: [coverageSources[1], coverageSources[4], coverageSources[5]] },
        ],
        answer: "The strongest pattern is graceful degradation rather than full mission restoration. Surviving vehicles first redistribute nearby cells, then rebalance globally only when connectivity permits. Graph redundancy before failure predicts recovery speed more reliably than raw fleet size.",
        sources: coverageSources.slice(0, 3),
        candidates: coverageCandidates,
        review: "pass",
        followUps: ["Compare recovery time", "Find contrasting evidence", "Focus on field deployments"],
      },
      {
        question: "What changes if communication is intermittent and we prioritize @Communication-Aware Swarm Replanning?",
        rationale: "Test whether local recovery remains dependable when global state arrives late or not at all.",
        queries: [
          { query: "intermittent communication UAV coverage recovery", status: "validated", sources: [coverageSources[1], coverageSources[3], coverageSources[4]] },
          { query: "swarm recovery no persistent connectivity", status: "retried once", revisedQuery: "delay-tolerant distributed aerial task allocation", sources: coverageSources.slice(2, 6) },
          { query: "local policy coverage restoration drone fleet", status: "kept, degraded", sources: [coverageSources[0], coverageSources[3], coverageSources[5]] },
        ],
        answer: "With intermittent links, the objective shifts from globally optimal coverage to bounded local inconsistency. Vehicles can retain short-lived neighborhood models and reconcile assignments after contact resumes. This duplicates some coverage near partition boundaries, but is safer than leaving regions uncovered.",
        sources: [coverageSources[4], coverageSources[1], coverageSources[2]],
        candidates: coverageCandidates.slice(1),
        review: [
          { claim: "Reconciliation restores coverage quickly", severity: "medium", note: "Recovery timing is supported by only one simulated fleet." },
          { claim: "Partition duplication is generally safer", severity: "low", note: "Distinguish prolonged partitions from brief packet loss." },
        ],
        followUps: ["Quantify duplicated coverage", "Compare partition durations", "Find hardware trials"],
      },
    ],
  },
  {
    id: "fleet-learning",
    title: "Federated learning with intermittent robot-fleet connectivity",
    project: "Fleet Intelligence",
    started: "2026-09-21T11:05",
    activity: "2026-09-21T16:18",
    considered: 11,
    added: 2,
    baselineAdded: "Asynchronous Fleet Learning Survey",
    turns: [
      {
        question: "Which federated learning strategies tolerate intermittent robot-fleet connectivity?",
        rationale: "Compare delayed aggregation, peer exchange, and update compression under recurring disconnections.",
        queries: [
          { query: "federated robot learning intermittent connectivity", status: "validated", sources: learningSources.slice(0, 4) },
          { query: "asynchronous aggregation mobile robot fleet", status: "validated", sources: [learningSources[0], learningSources[1], learningSources[5]] },
          { query: "compressed model exchange disconnected robots", status: "retried once", revisedQuery: "energy-aware federated update compression mobile robots", sources: learningSources.slice(2, 6) },
        ],
        answer: "Staleness-aware asynchronous aggregation is the most consistent fit for mobile fleets because it does not block on absent robots. Peer-to-peer gossip improves resilience when a coordinator is unreachable. Compression saves radio energy, although aggressive sparsification can amplify differences between local environments.",
        sources: learningSources.slice(0, 3),
        candidates: learningCandidates,
        review: "pass",
        followUps: ["Compare aggregation rules", "Focus on energy cost", "Find heterogeneous fleets"],
      },
      {
        question: "How should we handle stale updates when routes create predictable contact windows?",
        rationale: "Look for methods that use contact schedules rather than treating every delayed update identically.",
        queries: [
          { query: "route-aware staleness federated robot learning", status: "validated", sources: [learningSources[0], learningSources[1], learningSources[4]] },
          { query: "scheduled contacts asynchronous fleet aggregation", status: "retried once", revisedQuery: "mobility-predicted model aggregation robot fleets", sources: [learningSources[1], learningSources[3], learningSources[5]] },
          { query: "non-IID robot observations delayed updates", status: "kept, degraded", sources: learningSources.slice(2, 6) },
        ],
        answer: "Predictable contact windows support age weighting that also accounts for where observations were collected. Updates expected to arrive after the next contact can be buffered instead of discarded. Local drift checks remain important because freshness alone does not reveal whether two robots observed compatible conditions.",
        sources: [learningSources[1], learningSources[4], learningSources[5]],
        candidates: learningCandidates.slice(1),
        review: [
          { claim: "Route-aware buffering outperforms dropping", severity: "medium", note: "The comparison assumes contact schedules remain stable." },
          { claim: "Freshness cannot detect incompatible observations", severity: "low", note: "Clarify that this applies to heterogeneous local data." },
        ],
        followUps: ["Test schedule uncertainty", "Compare drift detectors", "Estimate radio savings"],
      },
    ],
  },
  {
    id: "path-planning",
    title: "Decentralized path planning under bandwidth limits",
    project: "Multi-UAV Coordination",
    started: "2026-09-18T14:20",
    activity: "2026-09-19T10:05",
    considered: 18,
    added: 4,
    baselineAdded: "Sparse-Message Planning Benchmarks",
    turns: [
      {
        question: "Compare decentralized path planning methods under strict bandwidth limits.",
        rationale: "Separate message representation, transmission triggers, and local collision safeguards.",
        queries: [
          { query: "decentralized multi-robot path planning bandwidth limits", status: "validated", sources: planningSources.slice(0, 4) },
          { query: "sparse communication collision avoidance robot teams", status: "validated", sources: planningSources.slice(1, 5) },
          { query: "compressed trajectory exchange distributed planning", status: "retried once", revisedQuery: "compact intent sharing multi-agent path planning", sources: [planningSources[0], planningSources[2], planningSources[4]] },
        ],
        answer: "Compact intent envelopes provide the best balance when bandwidth is predictable: agents share corridor, priority, and arrival windows rather than full trajectories. Event-triggered updates reduce traffic further. Local safe corridors are still necessary because delayed intent can otherwise create unsafe confidence.",
        sources: planningSources.slice(0, 3),
        candidates: planningCandidates,
        review: "pass",
        followUps: ["Compare message sizes", "Focus on collision bounds", "Find outdoor evaluations"],
      },
      {
        question: "Do priority tokens still work when packet delay is variable?",
        rationale: "Test token ownership, stale peer plans, and fallback behavior under non-uniform delay.",
        queries: [
          { query: "priority token path planning variable packet delay", status: "retried once", revisedQuery: "token-based right-of-way delayed multi-robot networks", sources: [planningSources[1], planningSources[4], planningSources[5]] },
          { query: "stale neighbor trajectories decentralized collision avoidance", status: "validated", sources: planningSources.slice(2, 6) },
          { query: "fallback safe corridors communication blackout", status: "kept, degraded", sources: [planningSources[0], planningSources[3], planningSources[5]] },
        ],
        answer: "Priority tokens remain useful if ownership expires and every robot can fall back to a conservative corridor. Without expiry, delayed token transfers can produce conflicting right-of-way beliefs. Receding-horizon checks limit this risk, but their safety margin reduces throughput as delay variance grows.",
        sources: [planningSources[4], planningSources[5], planningSources[3]],
        candidates: planningCandidates.slice(1),
        review: [
          { claim: "Expiring tokens prevent conflicting beliefs", severity: "high", note: "The evidence shows risk reduction, not complete prevention." },
          { claim: "Safety margins reduce throughput", severity: "low", note: "State that the measured effect grows with delay variance." },
        ],
        followUps: ["Find token expiry bounds", "Compare throughput loss", "Inspect worst-case delays"],
      },
    ],
  },
];

/** One row of the "Recent explorations" list. */
export type ExplorationSummary = {
  id: string;
  title: string;
  started: string;
  activity: string;
  added: number;
  exchanges: number;
  considered: number;
};

export const explorations: ExplorationSummary[] = explorationThreads.map(
  (thread) => ({
    id: thread.id,
    // The first question, not the thread's short name: the list is a way back
    // into a train of thought, and the question is what the user typed.
    title: thread.turns[0]?.question ?? thread.title,
    started: thread.started,
    activity: thread.activity,
    added: thread.added,
    exchanges: thread.turns.length,
    considered: thread.considered,
  })
);

/** The suggestion chips on the Explorer home composer. */
export const suggestions = [
  "How do multi-UAV systems maintain coverage after vehicle loss?",
  "Federated learning with intermittent robot-fleet connectivity",
  "Compare decentralized path planning under bandwidth limits",
  "What recovery policies work after losing two vehicles?",
];

/** The thread the home composer opens; every question leads to the same mock. */
export const DEMO_THREAD_ID = "uav-loss";

export function getExplorationThread(id: string) {
  return explorationThreads.find((thread) => thread.id === id);
}
