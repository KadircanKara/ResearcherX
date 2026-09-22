import type { Paper } from "./types";

/**
 * What this project can HONESTLY say about a paper's place in the retriever.
 *
 * The list endpoint (`GET /projects/{id}/papers`, `PaperOut`) returns
 * id/project_id/title/abstract/body/pdf_url/source/created_at and nothing
 * else. There is no chunk count, no embedding state, no ingest job and no
 * progress anywhere in the API, so none of that is derived here — a state
 * this module cannot stand behind is reported as unknown, never guessed.
 *
 * Two things ARE knowable:
 *
 *  - list-side, from `source` + the text fields. `create_paper` indexes a
 *    MANUAL paper carrying an abstract or a body inside the same transaction
 *    that writes the paper, and rolls the paper back if the embedding call
 *    fails — so a persisted manual paper with text had chunks written for it.
 *    A manual paper with neither has nothing to index, by the same route.
 *    Upload and link papers are ingested by a SEPARATE later request whose
 *    outcome nothing persists, so the list row says nothing about them at all.
 *
 *  - per paper, on demand, by asking for its chunk 0
 *    (`GET .../papers/{id}/chunks/0`). 200 means the retriever holds text for
 *    it under the CURRENT embedding model; 404 means it holds none. That is a
 *    real answer to the only question this screen asks, and it is why an
 *    "indexed on save" claim can still be overturned: chunks written under a
 *    previous `EMBEDDING_MODEL` are filtered out of every retrieval query and
 *    out of that probe alike.
 *
 * The probe is one request and answers for one paper, so it runs when a row is
 * opened — never in a sweep over the library on load.
 */
export type ProbeResult = "indexed" | "empty" | "unavailable";

/** Probe outcomes so far, keyed by paper id. Absent = never asked. */
export type ProbeMap = Record<string, ProbeResult | "checking" | undefined>;

export type PaperStateKind =
  /** Probe confirmed: the retriever holds chunks for this paper. */
  | "indexed"
  /** Manual paper with text: chunks were written with the row. Unverified. */
  | "expected"
  /** Upload/link, never probed. Nothing in the list response speaks to it. */
  | "unchecked"
  /** Probe in flight. */
  | "checking"
  /** Probe confirmed: the retriever holds nothing for this paper. */
  | "empty"
  /** Manual paper with no abstract and no body — nothing was ever indexed. */
  | "no-text"
  /** The probe itself failed. Says nothing about the paper. */
  | "unavailable";

export interface PaperState {
  kind: PaperStateKind;
  /** The State cell. */
  label: string;
  /** Which dot to draw. */
  tone: "on" | "idle" | "bad";
  /** True only where the claim rests on a probe or on a backend guarantee. */
  certain: boolean;
}

const STATES: Record<PaperStateKind, Omit<PaperState, "kind">> = {
  indexed: { label: "searchable", tone: "on", certain: true },
  expected: { label: "indexed on save", tone: "on", certain: true },
  unchecked: { label: "open to check", tone: "idle", certain: false },
  checking: { label: "checking…", tone: "idle", certain: false },
  empty: { label: "no indexed text", tone: "bad", certain: true },
  "no-text": { label: "no text to index", tone: "idle", certain: true },
  unavailable: { label: "couldn't check", tone: "idle", certain: false },
};

export function hasText(paper: Paper): boolean {
  return Boolean(paper.abstract?.trim() || paper.body?.trim());
}

export function paperState(paper: Paper, probe: ProbeMap[string]): PaperState {
  // A probe beats every list-side inference, in both directions: it is the
  // only signal that has actually asked the retriever.
  let kind: PaperStateKind;
  if (probe === "indexed" || probe === "empty" || probe === "checking") {
    kind = probe;
  } else if (paper.source === "manual") {
    kind = hasText(paper) ? "expected" : "no-text";
  } else if (probe === "unavailable") {
    kind = "unavailable";
  } else {
    kind = "unchecked";
  }
  return { kind, ...STATES[kind] };
}

/** The sentence the opened row shows under the state. */
export function stateDetail(state: PaperState): string {
  switch (state.kind) {
    case "indexed":
      return "The retriever holds text for this paper, so it can be searched and mentioned in a question.";
    case "expected":
      return "Its text was split and embedded when it was saved. Open it to confirm the retriever still holds it.";
    case "unchecked":
      return "Nothing in the library listing records whether this paper was indexed. Checking asks the retriever directly.";
    case "checking":
      return "Asking the retriever what it holds for this paper.";
    case "empty":
      return "The retriever holds nothing for this paper, so it cannot be searched or mentioned. If it is a scanned PDF there was no text to extract — run it through OCR and upload it again.";
    case "no-text":
      return "This paper was entered by hand with no abstract and no body, so there was nothing to index.";
    case "unavailable":
      return "The check itself failed, so this says nothing about the paper. Try opening the row again.";
  }
}

export interface LibrarySummary {
  total: number;
  /** States this module can stand behind, in rail order. */
  searchable: number;
  unchecked: number;
  attention: number;
}

export function summarize(papers: Paper[], probes: ProbeMap): LibrarySummary {
  let searchable = 0;
  let unchecked = 0;
  let attention = 0;
  for (const paper of papers) {
    const { kind } = paperState(paper, probes[paper.id]);
    if (kind === "indexed" || kind === "expected") searchable += 1;
    else if (kind === "empty") attention += 1;
    else unchecked += 1;
  }
  return { total: papers.length, searchable, unchecked, attention };
}

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

/**
 * The headline. It states the count, and the searchable count only once one
 * is actually established — an unprobed library of uploads knows nothing
 * about how many of its papers the retriever can reach, and saying "0 of them
 * searchable" there would be a measurement claim nobody made.
 */
export function libraryHeadline(summary: LibrarySummary): string {
  if (summary.total === 0) return "No papers yet";
  const papers = plural(summary.total, "paper");
  if (summary.searchable === 0) return `${papers} in this library`;
  if (summary.searchable === summary.total) {
    return summary.total === 1 ? "One paper, searchable" : `${papers}, all searchable`;
  }
  return `${papers}, ${summary.searchable} of them searchable`;
}

/** The rail's total line. Chunk counts are not in the API, so this is papers. */
export function railTotal(summary: LibrarySummary): string {
  return summary.total === 0 ? "Nothing here yet" : plural(summary.total, "paper");
}

export function sourceLine(paper: Paper): string {
  switch (paper.source) {
    case "upload":
      return "Uploaded PDF";
    case "link":
      return paper.pdf_url ? `Linked · ${hostOf(paper.pdf_url)}` : "Linked PDF";
    default:
      return "Entered by hand";
  }
}

function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "link";
  }
}

export function formatAdded(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}
