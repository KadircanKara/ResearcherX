import { describe, expect, it } from "vitest";
import {
  activityDay,
  conversationCount,
  groupTurns,
  questionCount,
  rowClickAction,
  startedAt,
  startedDay,
  toLocalStamp,
} from "./conversations";
import type { ChatMessage } from "./types";

// Every Date below is built from LOCAL components, so these assertions hold in
// any TZ the test runner happens to be in. Building them from a UTC ISO string
// instead would make the suite pass in London and fail in Istanbul.
const local = (y: number, m: number, d: number, h = 0, min = 0) =>
  new Date(y, m - 1, d, h, min);

let seq = 0;
const msg = (role: ChatMessage["role"]): ChatMessage => ({
  id: `m${seq++}`,
  role,
  content: "",
  citations: [],
  mentions: [],
  created_at: "2026-08-19T12:00:00+00:00",
});

describe("toLocalStamp", () => {
  it("zero-pads every field", () => {
    expect(toLocalStamp(local(2026, 1, 2, 3, 4))).toBe("2026-01-02T03:04");
  });
});

describe("startedDay", () => {
  it("is the prototype's day, month and year, never a clock", () => {
    expect(startedDay(local(2026, 9, 18, 8, 5).toISOString())).toBe("18 Sep 2026");
    expect(startedDay(local(2026, 1, 2, 23, 59).toISOString())).toBe("2 Jan 2026");
  });

  it("dates the reader's own wall clock, not UTC", () => {
    // Built from local components: whatever the runner's offset, a late
    // evening start stays on the local day it happened.
    expect(startedDay(local(2026, 9, 18, 23, 59).toISOString())).toBe("18 Sep 2026");
    expect(startedDay(local(2026, 9, 19, 0, 1).toISOString())).toBe("19 Sep 2026");
  });

  it("renders nothing rather than Invalid Date for a broken stamp", () => {
    expect(startedDay("nope")).toBe("");
    expect(startedDay("")).toBe("");
  });
});

describe("activityDay", () => {
  it("drops the year — the column is already about recent activity", () => {
    expect(activityDay(local(2026, 9, 18, 14, 2).toISOString())).toBe("18 Sep");
    expect(activityDay(local(2025, 12, 31, 23, 59).toISOString())).toBe("31 Dec");
  });

  it("renders nothing for a broken stamp", () => {
    expect(activityDay("not a date")).toBe("");
  });
});

describe("startedAt", () => {
  it("reads as the thread header's Started line", () => {
    expect(startedAt(local(2026, 9, 18, 8, 5).toISOString())).toBe("Started 18 Sep 2026");
  });

  it("renders nothing for a broken stamp", () => {
    expect(startedAt("")).toBe("");
  });
});

describe("conversationCount", () => {
  it("counts every case the same way, zero included", () => {
    expect(conversationCount(0)).toBe("0 conversations");
    expect(conversationCount(1)).toBe("1 conversation");
    expect(conversationCount(5)).toBe("5 conversations");
  });
});

describe("rowClickAction", () => {
  const plain = {
    editing: false,
    button: 0,
    metaKey: false,
    ctrlKey: false,
    shiftKey: false,
    altKey: false,
  };

  it("leaves every click to the link outside edit mode", () => {
    expect(rowClickAction(plain)).toBe("navigate");
    expect(rowClickAction({ ...plain, metaKey: true })).toBe("navigate");
    expect(rowClickAction({ ...plain, button: 1 })).toBe("navigate");
  });

  it("selects the row on a plain click in edit mode", () => {
    expect(rowClickAction({ ...plain, editing: true })).toBe("toggle");
  });

  it("still opens a new tab or window on a modified click in edit mode", () => {
    for (const key of ["metaKey", "ctrlKey", "shiftKey", "altKey"] as const) {
      expect(rowClickAction({ ...plain, editing: true, [key]: true })).toBe("navigate");
    }
  });

  it("never treats a non-primary button as a selection", () => {
    expect(rowClickAction({ ...plain, editing: true, button: 1 })).toBe("navigate");
    expect(rowClickAction({ ...plain, editing: true, button: 2 })).toBe("navigate");
  });
});

describe("questionCount", () => {
  it("counts what the reader asked, not the messages", () => {
    expect(questionCount([])).toBe("0 questions");
    expect(questionCount([msg("user"), msg("assistant")])).toBe("1 question");
    expect(
      questionCount([msg("user"), msg("assistant"), msg("user"), msg("assistant")])
    ).toBe("2 questions");
  });

  it("adds turns sent since the snapshot was fetched", () => {
    expect(questionCount([msg("user"), msg("assistant")], 2)).toBe("3 questions");
    expect(questionCount([], 1)).toBe("1 question");
  });
});

describe("groupTurns", () => {
  it("pairs each question with the answer that followed it", () => {
    const q1 = msg("user");
    const a1 = msg("assistant");
    const q2 = msg("user");
    const a2 = msg("assistant");
    expect(groupTurns([q1, a1, q2, a2])).toEqual([
      { key: q1.id, question: q1, answers: [a1] },
      { key: q2.id, question: q2, answers: [a2] },
    ]);
  });

  it("keeps a question whose answer has not landed yet", () => {
    const q = msg("user");
    expect(groupTurns([q])).toEqual([{ key: q.id, question: q, answers: [] }]);
  });

  it("never drops a message, whatever shape the history is in", () => {
    // A leading assistant message, and a question that got two answers:
    // neither is produced today, and neither may vanish if it ever is.
    const a0 = msg("assistant");
    const q = msg("user");
    const a1 = msg("assistant");
    const a2 = msg("assistant");
    const turns = groupTurns([a0, q, a1, a2]);
    expect(turns).toEqual([
      { key: a0.id, question: null, answers: [a0] },
      { key: q.id, question: q, answers: [a1, a2] },
    ]);
    const seen = turns.flatMap((t) => [...(t.question ? [t.question] : []), ...t.answers]);
    expect(seen).toHaveLength(4);
  });

  it("is empty for an empty conversation", () => {
    expect(groupTurns([])).toEqual([]);
  });
});
