import { describe, expect, it } from "vitest";
import {
  emptyMentionsNote,
  scopeLine,
  statusLabel,
  type RetrievingInfo,
} from "./chat-scope";

function info(over: Partial<RetrievingInfo> = {}): RetrievingInfo {
  return {
    paper_count: 6,
    history_hits: 0,
    scoped: false,
    scoped_count: 0,
    widened: false,
    empty_mentions: [],
    scope_source: "mention",
    scope_evidence: [],
    ...over,
  };
}

const flat = (segs: ReturnType<typeof scopeLine>) =>
  (segs ?? []).map((s) => s.text).join("");

describe("scopeLine", () => {
  it("says nothing before the retrieving event lands", () => {
    expect(scopeLine(null)).toBeNull();
  });

  it("quotes the reader's own phrase for a resolved scope", () => {
    const segs = scopeLine(
      info({
        scoped: true,
        scoped_count: 1,
        scope_source: "resolved",
        scope_evidence: ["Cooperative Multi-Target Search with UAV Swarms"],
      })
    );
    expect(flat(segs)).toBe(
      "Matched “Cooperative Multi-Target Search with UAV Swarms” in your question — searching 1 paper only."
    );
    // The phrase, and only the phrase, is emphasised.
    expect(segs?.filter((s) => s.emphasis).map((s) => s.text)).toEqual([
      "“Cooperative Multi-Target Search with UAV Swarms”",
    ]);
  });

  it("joins several resolved phrases and keeps each one emphasised", () => {
    const segs = scopeLine(
      info({
        scoped: true,
        scoped_count: 2,
        scope_source: "resolved",
        scope_evidence: ["by Yanmaz", "Voronoi Partitioning for Persistent Area"],
      })
    );
    expect(flat(segs)).toBe(
      "Matched “by Yanmaz”, “Voronoi Partitioning for Persistent Area” in your question — searching 2 papers only."
    );
    expect(segs?.filter((s) => s.emphasis)).toHaveLength(2);
  });

  it("reports a widened resolved scope as read, not as attempted", () => {
    const segs = scopeLine(
      info({
        scoped: true,
        scoped_count: 1,
        widened: true,
        scope_source: "resolved",
        scope_evidence: ["by Güven"],
      })
    );
    expect(flat(segs)).toBe(
      "Matched “by Güven” in your question — searching 1 paper and the rest of the library."
    );
  });

  it("falls back to a phrase-free wording when the backend sent no evidence", () => {
    const segs = scopeLine(
      info({ scoped: true, scoped_count: 2, scope_source: "resolved" })
    );
    expect(flat(segs)).toBe(
      "Your question named 2 papers — searching 2 papers only."
    );
    expect(segs?.some((s) => s.emphasis)).toBe(false);
  });

  it("tells a mention turn only whether the search stayed inside the picks", () => {
    expect(flat(scopeLine(info({ scoped: true, scoped_count: 2 })))).toBe(
      "You named 2 papers — searching 2 papers only."
    );
    expect(
      flat(scopeLine(info({ scoped: true, scoped_count: 2, widened: true })))
    ).toBe("You named 2 papers — searching 2 papers and the rest of the library.");
  });

  it("never claims a scope of zero papers", () => {
    // Papers were mentioned but all had been deleted: the backend reports
    // this widened, and "scoped to 0 papers" would claim a scope that never
    // applied.
    for (const source of ["mention", "resolved"] as const) {
      expect(
        flat(
          scopeLine(
            info({ scoped: true, scoped_count: 0, widened: true, scope_source: source })
          )
        )
      ).toBe("Mentions unavailable — searching the whole library.");
    }
  });

  it("states the real number of papers searched for a global turn", () => {
    expect(flat(scopeLine(info({ paper_count: 6 })))).toBe(
      "No paper named — searching all 6 papers."
    );
    expect(flat(scopeLine(info({ paper_count: 1 })))).toBe(
      "No paper named — searching all 1 paper."
    );
  });
});

describe("emptyMentionsNote", () => {
  it("is silent when every named paper returned something", () => {
    expect(emptyMentionsNote(info({ scoped: true, scoped_count: 2 }))).toBeNull();
    expect(emptyMentionsNote(null)).toBeNull();
  });

  it("names the papers that came back with nothing", () => {
    expect(
      emptyMentionsNote(info({ empty_mentions: ["Voronoi Partitioning", "NeMo-Mobility"] }))
    ).toBe("No excerpts from Voronoi Partitioning, NeMo-Mobility.");
  });
});

describe("statusLabel", () => {
  it("is silent at rest", () => {
    expect(statusLabel("idle", null)).toBeNull();
  });

  it("counts the papers actually being searched once the event lands", () => {
    expect(statusLabel("retrieving", null)).toBe("Searching the library");
    expect(statusLabel("retrieving", info({ paper_count: 4 }))).toBe("Searching 4 papers");
    expect(statusLabel("retrieving", info({ paper_count: 1 }))).toBe("Searching 1 paper");
  });

  it("names the other two phases", () => {
    expect(statusLabel("thinking", null)).toBe("Reading the question");
    expect(statusLabel("streaming", info())).toBe("Writing the answer");
  });
});
