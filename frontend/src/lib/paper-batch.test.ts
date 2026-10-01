import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./api-error";
import {
  MAX_BATCH,
  TITLE_MAX,
  addButtonLabel,
  NO_TEXT_WARNING,
  addableCount,
  closesAfterBatch,
  ingestWarning,
  linkFailure,
  overCapNotice,
  splitUploads,
  titleFromFilename,
  unsupportedNotice,
  uploadFailure,
  withTimeout,
  type BatchStatus,
} from "./paper-batch";

const file = (name: string) => ({ name });

describe("splitUploads", () => {
  it("keeps the five formats, whatever the extension's case, and counts the rest", () => {
    const names = ["a.pdf", "b.PDF", "c.docx", "d.md", "e.markdown", "f.TXT", "g.rtf"];
    const r = splitUploads([...names, "h.doc", "i.png", "README"].map(file), 0);
    expect(r.accepted.map((f) => f.name)).toEqual(names);
    expect(r.unsupported).toBe(3);
    expect(r.overCap).toBe(0);
  });

  it("caps the batch at what is left of it, counting the overflow", () => {
    const files = Array.from({ length: 5 }, (_, i) => file(`${i}.pdf`));
    const r = splitUploads(files, MAX_BATCH - 2);
    expect(r.accepted.map((f) => f.name)).toEqual(["0.pdf", "1.pdf"]);
    expect(r.overCap).toBe(3);
  });

  it("accepts nothing into a full batch, and never a negative room", () => {
    const r = splitUploads([file("a.pdf")], MAX_BATCH + 3);
    expect(r.accepted).toEqual([]);
    expect(r.overCap).toBe(1);
  });
});

describe("notices", () => {
  it("says how many were skipped and why, in both numbers", () => {
    expect(unsupportedNotice(1)).toBe(
      "1 file was not a PDF, DOCX, Markdown, TXT or RTF file and was skipped."
    );
    expect(unsupportedNotice(3)).toBe(
      "3 files were not PDF, DOCX, Markdown, TXT or RTF files and were skipped."
    );
    expect(overCapNotice(1)).toBe("1 file was over the 20-file cap and was skipped.");
    expect(overCapNotice(2)).toBe("2 files were over the 20-file cap and were skipped.");
  });
});

describe("titleFromFilename", () => {
  it("drops the extension and bounds the length", () => {
    expect(titleFromFilename("Swarm Search.PDF")).toBe("Swarm Search");
    expect(titleFromFilename("Draft v2.docx")).toBe("Draft v2");
    expect(titleFromFilename("notes.Markdown")).toBe("notes");
    expect(titleFromFilename("plain.txt")).toBe("plain");
    expect(titleFromFilename("old.rtf")).toBe("old");
    expect(titleFromFilename("archive.tar.gz")).toBe("archive.tar.gz");
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
    expect(uploadFailure(true)).toBe("Couldn't read or index this file.");
    expect(uploadFailure(false)).toContain("cleanup failed");
    expect(linkFailure({ cleanedUp: false, paywalled: true, unavailable: false })).toContain(
      "cleanup failed"
    );
  });

  it("shows the backend's reason for a refused file, and only for a 422", () => {
    const refused = new ApiError(422, "This file's contents don't match its .docx extension.");
    expect(uploadFailure(true, refused)).toBe(
      "This file's contents don't match its .docx extension."
    );
    expect(uploadFailure(false, refused)).toContain("cleanup failed");
    expect(uploadFailure(true, new ApiError(500, "boom"))).toBe(
      "Couldn't read or index this file."
    );
    expect(uploadFailure(true, new ApiError(422, null))).toBe(
      "Couldn't read or index this file."
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

describe("ingestWarning", () => {
  it("warns only when the upload stored no chunks", () => {
    expect(ingestWarning(0)).toBe(NO_TEXT_WARNING);
    expect(ingestWarning(1)).toBeNull();
    expect(ingestWarning(67)).toBeNull();
  });

  it("names the likely cause and the consequence", () => {
    expect(NO_TEXT_WARNING).toContain("scan");
    expect(NO_TEXT_WARNING).toContain("chat can't search it");
  });
});

describe("closesAfterBatch", () => {
  it("closes when every row was added cleanly", () => {
    expect(closesAfterBatch([{ status: "done" }, { status: "done", warning: undefined }])).toBe(
      true
    );
  });

  it("stays open while a warning would otherwise vanish unread", () => {
    expect(closesAfterBatch([{ status: "done" }, { status: "done", warning: NO_TEXT_WARNING }])).toBe(
      false
    );
  });

  it("stays open on a failure", () => {
    expect(closesAfterBatch([{ status: "done" }, { status: "failed" }])).toBe(false);
  });
});
