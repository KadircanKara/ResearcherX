import { plural } from "./format";

/**
 * The rules behind the Add papers dialog's three tabs.
 *
 * PURE and in `lib/` because vitest here runs in node with no jsdom: the
 * tabs themselves are components, and a rule worth a test cannot live
 * inside one.
 */

/** "A batch is capped at 20 files." */
export const MAX_BATCH = 20;

/** Longest title a row will accept. The backend allows more; this keeps a
 *  title a title rather than a pasted abstract. */
export const TITLE_MAX = 150;

// A hung request must never wedge the batch: without a bound the batch never
// resolves, the busy flag never clears, and the busy-guard leaves the dialog
// permanently undismissable. Generous enough for a large PDF upload + ingest:
// dev embedding on CPU in-container took ~47s for a 67-chunk paper
// (measured), and long papers scale past that. 120s left almost no margin.
export const ITEM_TIMEOUT_MS = 300_000;

export function withTimeout<T>(promise: Promise<T>, ms: number, label: string): Promise<T> {
  let timer: ReturnType<typeof setTimeout>;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error(`${label} timed out`)), ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer)) as Promise<T>;
}

/** An upload row's life, shown to the reader as the word itself. */
export type BatchStatus = "pending" | "extracting" | "ready" | "saving" | "done" | "failed";

/**
 * Which of `files` join a batch already holding `queued` rows: the PDFs, up
 * to the cap. Both kinds of refusal are COUNTED, never dropped silently —
 * the tab says how many files it skipped and why.
 */
export function splitPdfs<T extends { name: string }>(
  files: readonly T[],
  queued: number,
  max: number = MAX_BATCH
): { accepted: T[]; nonPdf: number; overCap: number } {
  const pdfs = files.filter((f) => f.name.toLowerCase().endsWith(".pdf"));
  const room = Math.max(0, max - queued);
  const accepted = pdfs.slice(0, room);
  return {
    accepted,
    nonPdf: files.length - pdfs.length,
    overCap: pdfs.length - accepted.length,
  };
}

/** The line under the drop zone for files that were not PDFs. */
export function nonPdfNotice(n: number): string {
  return n === 1
    ? "1 file was not a PDF and was skipped."
    : `${n} files were not PDFs and were skipped.`;
}

/** The line under the drop zone for PDFs past the cap. */
export function overCapNotice(n: number, max: number = MAX_BATCH): string {
  return n === 1
    ? `1 file was over the ${max}-file cap and was skipped.`
    : `${n} files were over the ${max}-file cap and were skipped.`;
}

/** The title a row falls back to when extraction suggests none. */
export function titleFromFilename(name: string): string {
  return name.replace(/\.pdf$/i, "").slice(0, TITLE_MAX);
}

/**
 * How many rows the Add button would still add: everything not already
 * added and not failed. A failed row counts again once Retry returns it to
 * `ready`.
 */
export function addableCount(items: readonly { status: BatchStatus }[]): number {
  return items.filter((it) => it.status !== "failed" && it.status !== "done").length;
}

export function addButtonLabel(count: number, submitting: boolean): string {
  return submitting ? "Adding…" : `Add ${plural(count, "paper")}`;
}

/**
 * Why an uploaded PDF was not added. A failed compensating delete is said
 * distinctly: a generic "failed" there would let a retry create the paper
 * again and leave the first one orphaned.
 */
export function uploadFailure(cleanedUp: boolean): string {
  return cleanedUp
    ? "Couldn't read or index this PDF."
    : "Couldn't index this PDF, and cleanup failed — check the paper list for a leftover entry.";
}

/**
 * Why a linked paper was not added. Paywalled is a normal outcome for a URL,
 * not a crash, and indexing being down is not the link's fault — saying
 * "couldn't fetch" there sends the reader to re-check a URL that was fine.
 */
export function linkFailure(opts: {
  cleanedUp: boolean;
  paywalled: boolean;
  unavailable: boolean;
}): string {
  if (!opts.cleanedUp) {
    return "Couldn't fetch this paper, and cleanup failed — check the paper list for a leftover entry.";
  }
  if (opts.paywalled) return "Paywalled — upload the PDF instead.";
  if (opts.unavailable) return "Indexing is temporarily unavailable. Try again later.";
  return "Couldn't fetch this paper.";
}
