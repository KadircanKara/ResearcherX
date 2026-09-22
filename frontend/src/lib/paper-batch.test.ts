import { afterEach, describe, expect, it, vi } from "vitest";
import {
  MAX_BATCH,
  TITLE_MAX,
  addButtonLabel,
  addableCount,
  linkFailure,
  nonPdfNotice,
  overCapNotice,
  splitPdfs,
  titleFromFilename,
  uploadFailure,
  withTimeout,
  type BatchStatus,
} from "./paper-batch";

const file = (name: string) => ({ name });

describe("splitPdfs", () => {
  it("keeps PDFs, whatever the extension's case, and counts the rest", () => {
    const r = splitPdfs([file("a.pdf"), file("b.PDF"), file("notes.txt")], 0);
    expect(r.accepted.map((f) => f.name)).toEqual(["a.pdf", "b.PDF"]);
    expect(r.nonPdf).toBe(1);
    expect(r.overCap).toBe(0);
  });

  it("caps the batch at what is left of it, counting the overflow", () => {
    const files = Array.from({ length: 5 }, (_, i) => file(`${i}.pdf`));
    const r = splitPdfs(files, MAX_BATCH - 2);
    expect(r.accepted.map((f) => f.name)).toEqual(["0.pdf", "1.pdf"]);
    expect(r.overCap).toBe(3);
  });

  it("accepts nothing into a full batch, and never a negative room", () => {
    const r = splitPdfs([file("a.pdf")], MAX_BATCH + 3);
    expect(r.accepted).toEqual([]);
    expect(r.overCap).toBe(1);
  });
});

describe("notices", () => {
  it("says how many were skipped and why, in both numbers", () => {
    expect(nonPdfNotice(1)).toBe("1 file was not a PDF and was skipped.");
    expect(nonPdfNotice(3)).toBe("3 files were not PDFs and were skipped.");
    expect(overCapNotice(1)).toBe("1 file was over the 20-file cap and was skipped.");
    expect(overCapNotice(2)).toBe("2 files were over the 20-file cap and were skipped.");
  });
});

describe("titleFromFilename", () => {
  it("drops the extension and bounds the length", () => {
    expect(titleFromFilename("Swarm Search.PDF")).toBe("Swarm Search");
    expect(titleFromFilename(`${"x".repeat(200)}.pdf`)).toHaveLength(TITLE_MAX);
  });
});

describe("addableCount and its label", () => {
  const rows = (...s: BatchStatus[]) => s.map((status) => ({ status }));

  it("counts neither failed nor already-added rows", () => {
    expect(addableCount(rows("pending", "extracting", "ready", "saving", "done", "failed"))).toBe(
      4
    );
  });

  it("reads as the prototype's button", () => {
    expect(addButtonLabel(1, false)).toBe("Add 1 paper");
    expect(addButtonLabel(3, false)).toBe("Add 3 papers");
    expect(addButtonLabel(0, false)).toBe("Add 0 papers");
    expect(addButtonLabel(3, true)).toBe("Adding…");
  });
});

describe("failure messages", () => {
  it("says a failed cleanup distinctly, for both sources", () => {
    expect(uploadFailure(true)).toBe("Couldn't read or index this PDF.");
    expect(uploadFailure(false)).toContain("cleanup failed");
    expect(linkFailure({ cleanedUp: false, paywalled: true, unavailable: false })).toContain(
      "cleanup failed"
    );
  });

  it("names a paywall and an indexing outage rather than blaming the link", () => {
    expect(linkFailure({ cleanedUp: true, paywalled: true, unavailable: false })).toBe(
      "Paywalled — upload the PDF instead."
    );
    expect(linkFailure({ cleanedUp: true, paywalled: false, unavailable: true })).toBe(
      "Indexing is temporarily unavailable. Try again later."
    );
    expect(linkFailure({ cleanedUp: true, paywalled: false, unavailable: false })).toBe(
      "Couldn't fetch this paper."
    );
  });
});

describe("withTimeout", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("passes a prompt result through", async () => {
    await expect(withTimeout(Promise.resolve(7), 1000, "x")).resolves.toBe(7);
  });

  it("rejects a request that never answers, naming it", async () => {
    vi.useFakeTimers();
    const hung = withTimeout(new Promise<never>(() => {}), 1000, "ingestPaper");
    const settled = expect(hung).rejects.toThrow("ingestPaper timed out");
    await vi.advanceTimersByTimeAsync(1000);
    await settled;
  });
});
