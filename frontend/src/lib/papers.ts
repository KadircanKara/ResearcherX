import { formatDate, plural } from "./format";
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
  /** Probed indexed, or a manual paper indexed inside its own create. */
  searchable: number;
  /** Nobody has asked the retriever yet, or the question is in flight. */
  unchecked: number;
  /** The retriever answered, and holds nothing. */
  attention: number;
}

/**
 * The rail's three counts.
 *
 * They do NOT always sum to `total`, on purpose: a hand-typed paper with no
 * text has nothing to check and nothing wrong with it, and a failed probe is
 * a claim about the check rather than about the paper — neither is "not
 * checked yet" and neither needs the reader's attention.
 */
export function summarize(papers: Paper[], probes: ProbeMap): LibrarySummary {
  let searchable = 0;
  let unchecked = 0;
  let attention = 0;
  for (const paper of papers) {
    const { kind } = paperState(paper, probes[paper.id]);
    if (kind === "indexed" || kind === "expected") searchable += 1;
    else if (kind === "unchecked" || kind === "checking") unchecked += 1;
    else if (kind === "empty") attention += 1;
  }
  return { total: papers.length, searchable, unchecked, attention };
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
  if (summary.searchable === summary.total) return `${papers}, all searchable`;
  return `${papers}, ${summary.searchable} of them searchable`;
}

/**
 * The table's Retriever cell: what the retriever is KNOWN to hold.
 *
 * Deliberately narrower than the State cell beside it. "holds text" is only
 * ever said on the back of a probe, because that is the only signal that has
 * asked the retriever — an `expected` paper (manual, indexed inside its own
 * create transaction) reads "—" here even though its State says "indexed on
 * save", since nothing has confirmed the retriever still holds it under the
 * CURRENT embedding model. `no-text` is the one un-probed "holds nothing":
 * a hand-typed paper with no abstract and no body had nothing to index in
 * the first place, so there is no claim being made about a retrieval that
 * happened.
 */
export function retrieverLabel(state: PaperState): string {
  switch (state.kind) {
    case "indexed":
      return "holds text";
    case "empty":
    case "no-text":
      return "holds nothing";
    default:
      return "—";
  }
}

/**
 * The header's right-hand meta line.
 *
 * Reads `created_at` off the papers themselves rather than taking a
 * pre-computed date, so the "nothing yet" case cannot be reached with a
 * stale stamp still in hand.
 */
export function lastAddedLabel(papers: readonly Paper[]): string {
  const latest = papers.reduce<string | null>(
    (newest, paper) =>
      newest === null || paper.created_at > newest ? paper.created_at : newest,
    null
  );
  return latest === null ? "Nothing added yet" : `Last added ${formatDate(latest)}`;
}

/** The row's second line: where the paper came from. */
export function sourceLine(paper: Paper): string {
  switch (paper.source) {
    case "upload":
      return "Uploaded PDF";
    case "link":
      return `Linked · ${hostOf(paper.pdf_url) ?? "unknown host"}`;
    default:
      return "Entered by hand";
  }
}

/** `https://www.arxiv.org/abs/1` → `arxiv.org`; null when there is no host. */
export function hostOf(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    return new URL(url).hostname.replace(/^www\./, "") || null;
  } catch {
    return null;
  }
}

const ABSTRACT_MAX = 320;

/** The opened row's abstract quote: the first 320 characters, then "…". */
export function abstractExcerpt(abstract: string): string {
  if (abstract.length <= ABSTRACT_MAX) return abstract;
  return `${abstract.slice(0, ABSTRACT_MAX).trimEnd()}…`;
}
