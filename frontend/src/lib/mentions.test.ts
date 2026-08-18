import { describe, expect, it } from "vitest";
import {
  findMentionQuery,
  insertMention,
  matchPapers,
  reconcileMentions,
  type Mention,
} from "./mentions";
import type { Paper } from "./types";

const paper = (id: string, title: string) => ({ id, title }) as Paper;

describe("findMentionQuery", () => {
  it("finds the query when @ starts a word", () => {
    expect(findMentionQuery("tell me about @coop", 19)).toEqual({ query: "coop", start: 14 });
  });

  it("ignores @ inside a word, so an email address never opens the dropdown", () => {
    expect(findMentionQuery("mail me@example.com", 19)).toBeNull();
  });

  it("closes the query at whitespace", () => {
    expect(findMentionQuery("@coop search now", 16)).toBeNull();
  });

  it("finds a bare @ with no query yet", () => {
    expect(findMentionQuery("what about @", 12)).toEqual({ query: "", start: 11 });
  });
});

describe("insertMention", () => {
  it("replaces the @query with the full title and a trailing space", () => {
    const out = insertMention("tell me about @coop", 14, 19, "Cooperative Search");
    expect(out.text).toBe("tell me about @Cooperative Search ");
    expect(out.caret).toBe(out.text.length);
  });
});

describe("reconcileMentions", () => {
  const a: Mention = { paperId: "p1", title: "Cooperative Search" };
  const b: Mention = { paperId: "p2", title: "Federated Sky" };

  it("keeps a mention while its @title is present", () => {
    expect(reconcileMentions("about @Cooperative Search now", [a])).toEqual([a]);
  });

  it("drops a mention whose text was deleted", () => {
    expect(reconcileMentions("about nothing", [a])).toEqual([]);
  });

  it("drops a mention whose text was partially deleted", () => {
    expect(reconcileMentions("about @Cooperative Sea", [a])).toEqual([]);
  });

  it("drops only the deleted mention when two unrelated ones are bound", () => {
    expect(reconcileMentions("compare @Cooperative Search and @Federated Sky", [a, b])).toEqual([
      a,
      b,
    ]);
    expect(reconcileMentions("compare @Federated Sky with nothing", [a, b])).toEqual([b]);
  });

  it("keeps two papers sharing a title only while both occurrences stand", () => {
    const dupA: Mention = { paperId: "p1", title: "Same Title" };
    const dupB: Mention = { paperId: "p2", title: "Same Title" };
    expect(reconcileMentions("@Same Title and @Same Title", [dupA, dupB])).toEqual([dupA, dupB]);
    expect(reconcileMentions("@Same Title alone", [dupA, dupB])).toEqual([dupA]);
  });

  it("never invents a mention from a hand-typed title", () => {
    // Only a dropdown selection binds an id. Ambiguity is never resolved by
    // guessing — the same rule the backend follows everywhere.
    expect(reconcileMentions("@Cooperative Search", [])).toEqual([]);
  });
});

describe("matchPapers", () => {
  const papers = [
    paper("p1", "Cooperative Multi-Target Search"),
    paper("p2", "Federated Learning in the Sky"),
    paper("p3", "Deep RL for Cooperative Control"),
  ];

  it("matches case-insensitively anywhere in the title", () => {
    expect(matchPapers(papers, "cooperative").map((p) => p.id)).toEqual(["p1", "p3"]);
  });

  it("ranks an earlier match position first", () => {
    expect(matchPapers(papers, "co")[0].id).toBe("p1");
  });

  it("returns everything for an empty query, capped", () => {
    expect(matchPapers(papers, "", 2)).toHaveLength(2);
  });
});

describe("reconcileMentions – overlapping titles (fix round 1)", () => {
  it("overlap: longest title claims the span, shorter one cannot reuse it", () => {
    const shorter: Mention = { paperId: "p1", title: "Search" };
    const longer: Mention = { paperId: "p2", title: "Search Methods" };
    // Both bound, text contains the longer title → only the longer mention survives
    const result = reconcileMentions("@Search Methods", [shorter, longer]);
    expect(result).toEqual([longer]);
  });

  it("non-overlap regression: @Search followed by prose must keep the mention", () => {
    const mention: Mention = { paperId: "p1", title: "Search" };
    // User typed @Search (bound), then added " Methods are useful" as prose
    expect(reconcileMentions("@Search Methods are useful", [mention])).toEqual([mention]);
  });

  it("identical titles still match by count: two bound, two occurrences → both survive", () => {
    const m1: Mention = { paperId: "p1", title: "Identity" };
    const m2: Mention = { paperId: "p2", title: "Identity" };
    expect(reconcileMentions("@Identity and @Identity", [m1, m2])).toEqual([m1, m2]);
  });

  it("identical titles by count: two bound, one occurrence → only first survives", () => {
    const m1: Mention = { paperId: "p1", title: "Identity" };
    const m2: Mention = { paperId: "p2", title: "Identity" };
    expect(reconcileMentions("@Identity only", [m1, m2])).toEqual([m1]);
  });

  it("original order preserved: three mentions in input order, not sorted order", () => {
    // Bind in order: short, long, medium. Sorted would be: long, medium, short.
    const short: Mention = { paperId: "p1", title: "X" };
    const long: Mention = { paperId: "p2", title: "Extra Long Title" };
    const medium: Mention = { paperId: "p3", title: "Medium" };
    const input = [short, long, medium];
    // All three are present in the text without overlap
    const text = "@X and @Extra Long Title with @Medium here";
    const result = reconcileMentions(text, input);
    // Result must be in INPUT order, not sorted order
    expect(result).toEqual([short, long, medium]);
  });
});

describe("insertMention – fix round 1", () => {
  it("with trailing text already starting with a space: exactly one space between title and text", () => {
    const out = insertMention("look at @coop please respond", 8, 13, "Cooperative Search");
    expect(out.text).toBe("look at @Cooperative Search please respond");
    expect(out.caret).toBe("look at @Cooperative Search ".length);
  });

  it("at end of text: trailing space is added", () => {
    const out = insertMention("look at @coop", 8, 13, "Cooperative Search");
    expect(out.text).toBe("look at @Cooperative Search ");
    expect(out.caret).toBe(out.text.length);
  });
});
