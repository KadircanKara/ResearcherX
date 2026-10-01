import { formatDate, plural } from "./format";
import type { Paper } from "./types";

/**
 * What this project can say about a paper's place in the retriever.
 *
 * The list endpoint (`GET /projects/{id}/papers`, `PaperOut`) carries
 * `chunk_count`: the rows stored for the paper under the CURRENT embedding
 * model, counted server-side in one grouped query. That is exactly what
 * retrieval can search -- chunks written under a previous `EMBEDDING_MODEL`
 * are filtered out of every retrieval query and out of this count alike -- so
 * the state is read straight off it, for every row, with no request per paper.
 */
export type PaperStateKind =
  /** The retriever holds chunks for this paper. */
  | "indexed"
  /** The retriever holds nothing, though the paper had (or should have had) text. */
  | "empty"
  /** Manual paper with no abstract and no body: there was nothing to index. */
  | "no-text";

export interface PaperState {
  kind: PaperStateKind;
  /** The State cell. */
  label: string;
  /** Which dot to draw. */
  tone: "on" | "idle" | "bad";
  /** Chunks the retriever holds for this paper under the current model. */
  chunks: number;
}

const STATES: Record<PaperStateKind, Omit<PaperState, "kind" | "chunks">> = {
  indexed: { label: "searchable", tone: "on" },
  empty: { label: "no indexed text", tone: "bad" },
  "no-text": { label: "no text to index", tone: "idle" },
};

export function hasText(paper: Paper): boolean {
  return Boolean(paper.abstract?.trim() || paper.body?.trim());
}

export function paperState(paper: Paper): PaperState {
  const chunks = paper.chunk_count;
  let kind: PaperStateKind;
  if (chunks > 0) kind = "indexed";
  // A hand-typed paper with no text had nothing to index: not a fault.
  else if (paper.source === "manual" && !hasText(paper)) kind = "no-text";
  // Everything else with zero chunks is a paper chat cannot find: a scanned
  // upload, or text indexed under an embedding model no longer configured.
  else kind = "empty";
  return { kind, chunks, ...STATES[kind] };
}

/** The sentence the opened row shows under the state. */
export function stateDetail(state: PaperState): string {
  switch (state.kind) {
    case "indexed":
      return "The retriever holds text for this paper, so it can be searched and mentioned in a question.";
    case "empty":
      return "The retriever holds nothing for this paper, so it cannot be searched or mentioned. If it is a scanned PDF there was no text to extract — run it through OCR and upload it again.";
    case "no-text":
      return "This paper was entered by hand with no abstract and no body, so there was nothing to index.";
  }
}

export interface LibrarySummary {
  total: number;
  /** The retriever holds chunks for it. */
  searchable: number;
  /** The retriever holds nothing for a paper that should have text. */
  attention: number;
}

/**
 * The rail's counts.
 *
 * They do NOT always sum to `total`, on purpose: a hand-typed paper with no
 * text is neither searchable nor anything the reader needs to fix.
 */
export function summarize(papers: readonly Paper[]): LibrarySummary {
  let searchable = 0;
  let attention = 0;
  for (const paper of papers) {
    const { kind } = paperState(paper);
    if (kind === "indexed") searchable += 1;
    else if (kind === "empty") attention += 1;
  }
  return { total: papers.length, searchable, attention };
}

/** The headline: the count, and how much of it chat can search. */
export function libraryHeadline(summary: LibrarySummary): string {
  if (summary.total === 0) return "No papers yet";
  const papers = plural(summary.total, "paper");
  if (summary.searchable === 0) return `${papers}, none searchable`;
  if (summary.searchable === summary.total) return `${papers}, all searchable`;
  return `${papers}, ${summary.searchable} of them searchable`;
}

/** The table's Retriever cell: how much the retriever holds for the paper. */
export function retrieverLabel(state: PaperState): string {
  return state.chunks > 0 ? plural(state.chunks, "chunk") : "holds nothing";
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
