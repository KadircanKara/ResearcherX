import { describe, expect, it } from "vitest";
import {
  abstractExcerpt,
  hasText,
  hostOf,
  lastAddedLabel,
  libraryHeadline,
  paperState,
  retrieverLabel,
  sourceLine,
  stateDetail,
  summarize,
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
    resolved_pdf_url: null,
    has_pdf: false,
    chunk_count: 0,
    source: "upload" as PaperSource,
    created_at: "2026-08-14T09:30:00Z",
    ...over,
  };
}

describe("paperState", () => {
  it("calls any paper with chunks searchable", () => {
    for (const source of ["upload", "link", "manual"] as const) {
      const s = paperState(paper({ source, abstract: "a", chunk_count: 3 }));
      expect(s.kind).toBe("indexed");
      expect(s.label).toBe("searchable");
      expect(s.tone).toBe("on");
      expect(s.chunks).toBe(3);
    }
  });

  it("counts a body as text too", () => {
    expect(hasText(paper({ body: "  full text  " }))).toBe(true);
    expect(hasText(paper({ abstract: "   " }))).toBe(false);
  });

  it("flags an upload or link with no chunks: chat cannot search it", () => {
    for (const source of ["upload", "link"] as const) {
      const s = paperState(paper({ source }));
      expect(s.kind).toBe("empty");
      expect(s.label).toBe("no indexed text");
      expect(s.tone).toBe("bad");
    }
  });

  it("calls a manual paper with no text nothing-to-index, not a failure", () => {
    const s = paperState(paper({ source: "manual" }));
    expect(s.kind).toBe("no-text");
    expect(s.tone).toBe("idle");
  });

  it("flags a manual paper whose text has no chunks under the current model", () => {
    const s = paperState(paper({ source: "manual", abstract: "a" }));
    expect(s.kind).toBe("empty");
    expect(s.tone).toBe("bad");
  });

  it("explains a scan in the opened row", () => {
    expect(stateDetail(paperState(paper()))).toContain("scanned PDF");
  });
});

describe("summarize", () => {
  const papers = [
    paper({ id: "a", source: "manual", abstract: "x", chunk_count: 1 }),
    paper({ id: "b", source: "upload", chunk_count: 12 }),
    paper({ id: "c", source: "upload" }), // empty
    paper({ id: "d", source: "manual" }), // no-text
  ];

  it("counts searchable and attention, and puts no-text in neither", () => {
    expect(summarize(papers)).toEqual({ total: 4, searchable: 2, attention: 1 });
  });
});

describe("libraryHeadline", () => {
  it("is empty-handed when the library is", () => {
    expect(libraryHeadline(summarize([]))).toBe("No papers yet");
  });

  it("says so when nothing is searchable", () => {
    const s = summarize([paper({ id: "a" }), paper({ id: "b" })]);
    expect(libraryHeadline(s)).toBe("2 papers, none searchable");
  });

  it("states how much of the library is searchable", () => {
    const s = summarize([
      paper({ id: "a", chunk_count: 2 }),
      paper({ id: "b", chunk_count: 5 }),
      paper({ id: "c" }),
    ]);
    expect(libraryHeadline(s)).toBe("3 papers, 2 of them searchable");
  });

  it("says so when every paper is searchable", () => {
    const s = summarize([paper({ id: "a", chunk_count: 1 }), paper({ id: "b", chunk_count: 1 })]);
    expect(libraryHeadline(s)).toBe("2 papers, all searchable");
    expect(libraryHeadline(summarize([paper({ id: "a", chunk_count: 4 })]))).toBe(
      "1 paper, all searchable"
    );
  });
});

describe("row secondary line", () => {
  it("names the real source, since authors and venue are not in the API", () => {
    expect(sourceLine(paper({ source: "upload" }))).toBe("Uploaded PDF");
    expect(sourceLine(paper({ source: "manual" }))).toBe("Entered by hand");
    expect(sourceLine(paper({ source: "link", pdf_url: "https://www.arxiv.org/abs/1" }))).toBe(
      "Linked · arxiv.org"
    );
    expect(sourceLine(paper({ source: "link", pdf_url: "not a url" }))).toBe(
      "Linked · unknown host"
    );
    expect(sourceLine(paper({ source: "link" }))).toBe("Linked · unknown host");
  });

  it("reads a host without inventing one", () => {
    expect(hostOf("https://www.arxiv.org/abs/1")).toBe("arxiv.org");
    expect(hostOf("not a url")).toBeNull();
    expect(hostOf(null)).toBeNull();
  });
});

describe("abstractExcerpt", () => {
  it("leaves a short abstract alone", () => {
    expect(abstractExcerpt("Short.")).toBe("Short.");
    expect(abstractExcerpt("x".repeat(320))).toBe("x".repeat(320));
  });

  it("cuts at 320 characters, trims the cut and marks it", () => {
    const cut = abstractExcerpt(`${"a".repeat(318)}  ${"b".repeat(40)}`);
    expect(cut).toBe(`${"a".repeat(318)}…`);
  });
});

describe("retrieverLabel", () => {
  it("states the chunk count for a searchable paper", () => {
    expect(retrieverLabel(paperState(paper({ chunk_count: 1 })))).toBe("1 chunk");
    expect(retrieverLabel(paperState(paper({ chunk_count: 42 })))).toBe("42 chunks");
  });

  it("says holds nothing for both ways of holding nothing", () => {
    expect(retrieverLabel(paperState(paper()))).toBe("holds nothing");
    expect(retrieverLabel(paperState(paper({ source: "manual" })))).toBe("holds nothing");
  });
});

describe("lastAddedLabel", () => {
  it("names the newest paper's day", () => {
    expect(
      lastAddedLabel([
        paper({ id: "a", created_at: "2026-08-14T09:30:00Z" }),
        paper({ id: "b", created_at: "2026-08-29T11:00:00Z" }),
        paper({ id: "c", created_at: "2026-07-01T11:00:00Z" }),
      ])
    ).toBe("Last added 29 Aug 2026");
  });

  it("says nothing was added rather than formatting a date it does not have", () => {
    expect(lastAddedLabel([])).toBe("Nothing added yet");
  });
});
