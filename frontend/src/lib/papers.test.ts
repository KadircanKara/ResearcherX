import { describe, expect, it } from "vitest";
import {
  formatAdded,
  hasText,
  libraryHeadline,
  paperState,
  railTotal,
  sourceLine,
  stateDetail,
  summarize,
  type ProbeMap,
} from "./papers";
import type { Paper, PaperSource } from "./types";

function paper(over: Partial<Paper> = {}): Paper {
  return {
    id: "p1",
    project_id: "proj",
    title: "Cooperative Multi-Target Search with UAV Swarms",
    abstract: null,
    body: null,
    pdf_url: null,
    source: "upload" as PaperSource,
    created_at: "2026-08-14T09:30:00Z",
    ...over,
  };
}

describe("paperState", () => {
  it("calls a manual paper carrying text indexed, because create_paper wrote its chunks", () => {
    const s = paperState(paper({ source: "manual", abstract: "An abstract." }), undefined);
    expect(s.kind).toBe("expected");
    expect(s.certain).toBe(true);
    expect(s.tone).toBe("on");
  });

  it("counts a body as text too", () => {
    expect(hasText(paper({ body: "  full text  " }))).toBe(true);
    expect(hasText(paper({ abstract: "   " }))).toBe(false);
  });

  it("calls a manual paper with no text nothing-to-index, not a failure", () => {
    const s = paperState(paper({ source: "manual" }), undefined);
    expect(s.kind).toBe("no-text");
    expect(s.tone).toBe("idle");
  });

  it("says nothing about an unprobed upload — the list response does not carry it", () => {
    const s = paperState(paper({ source: "upload" }), undefined);
    expect(s.kind).toBe("unchecked");
    expect(s.certain).toBe(false);
  });

  it("lets a probe overturn the manual-paper inference, since the model may have changed", () => {
    const s = paperState(paper({ source: "manual", abstract: "a" }), "empty");
    expect(s.kind).toBe("empty");
    expect(s.tone).toBe("bad");
  });

  it("reports a failed probe as a failed check, not as a paper with no text", () => {
    const s = paperState(paper({ source: "link" }), "unavailable");
    expect(s.kind).toBe("unavailable");
    expect(s.certain).toBe(false);
    expect(stateDetail(s)).toContain("says nothing about the paper");
  });

  it("shows the probe in flight", () => {
    expect(paperState(paper(), "checking").kind).toBe("checking");
  });
});

describe("summarize", () => {
  const papers = [
    paper({ id: "a", source: "manual", abstract: "x" }), // expected
    paper({ id: "b", source: "upload" }), // unchecked
    paper({ id: "c", source: "upload" }), // probed indexed
    paper({ id: "d", source: "upload" }), // probed empty
    paper({ id: "e", source: "manual" }), // no-text
  ];
  const probes: ProbeMap = { c: "indexed", d: "empty" };

  it("counts only states it can stand behind as searchable", () => {
    expect(summarize(papers, probes)).toEqual({
      total: 5,
      searchable: 2,
      unchecked: 2,
      attention: 1,
    });
  });

  it("puts a no-text manual paper with the unchecked, not with the failures", () => {
    expect(summarize([paper({ source: "manual" })], {}).attention).toBe(0);
    expect(summarize([paper({ source: "manual" })], {}).unchecked).toBe(1);
  });
});

describe("libraryHeadline", () => {
  it("is empty-handed when the library is", () => {
    expect(libraryHeadline(summarize([], {}))).toBe("No papers yet");
  });

  it("claims no searchable count when nothing has established one", () => {
    const s = summarize([paper({ id: "a" }), paper({ id: "b" })], {});
    expect(libraryHeadline(s)).toBe("2 papers in this library");
  });

  it("states the fact that matters once part of the library is known searchable", () => {
    const s = summarize(
      [paper({ id: "a" }), paper({ id: "b" }), paper({ id: "c" })],
      { a: "indexed", b: "indexed" }
    );
    expect(libraryHeadline(s)).toBe("3 papers, 2 of them searchable");
  });

  it("says so when every paper is searchable", () => {
    const s = summarize([paper({ id: "a" }), paper({ id: "b" })], {
      a: "indexed",
      b: "indexed",
    });
    expect(libraryHeadline(s)).toBe("2 papers, all searchable");
    expect(libraryHeadline(summarize([paper({ id: "a" })], { a: "indexed" }))).toBe(
      "One paper, searchable"
    );
  });

  it("agrees with the rail total", () => {
    expect(railTotal(summarize([paper()], {}))).toBe("1 paper");
    expect(railTotal(summarize([], {}))).toBe("Nothing here yet");
  });
});

describe("row secondary line", () => {
  it("names the real source, since authors and venue are not in the API", () => {
    expect(sourceLine(paper({ source: "upload" }))).toBe("Uploaded PDF");
    expect(sourceLine(paper({ source: "manual" }))).toBe("Entered by hand");
    expect(sourceLine(paper({ source: "link", pdf_url: "https://www.arxiv.org/abs/1" }))).toBe(
      "Linked · arxiv.org"
    );
    expect(sourceLine(paper({ source: "link", pdf_url: "not a url" }))).toBe("Linked · link");
    expect(sourceLine(paper({ source: "link" }))).toBe("Linked PDF");
  });
});

describe("formatAdded", () => {
  it("formats an ISO date and refuses to invent one", () => {
    expect(formatAdded("2026-08-14T09:30:00Z")).toBe("14 Aug 2026");
    expect(formatAdded("nonsense")).toBe("—");
  });
});
