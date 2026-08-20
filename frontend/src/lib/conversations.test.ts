import { describe, expect, it } from "vitest";
import {
  activityLabel,
  conversationCount,
  groupTurns,
  questionCount,
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

describe("activityLabel", () => {
  const now = local(2026, 8, 19, 16, 30);

  it("uses the clock only while today and yesterday still locate it", () => {
    expect(activityLabel(local(2026, 8, 19, 14, 2).toISOString(), now)).toBe("Today, 14:02");
    expect(activityLabel(local(2026, 8, 18, 9, 41).toISOString(), now)).toBe(
      "Yesterday, 09:41"
    );
    expect(activityLabel(local(2026, 8, 16, 9, 41).toISOString(), now)).toBe("16 Aug 2026");
  });

  it("reads the reader's own wall clock, not UTC", () => {
    // The same instant, expressed as a local Date: whatever the runner's
    // offset, this must land on "today" and on the local hour and minute.
    const at = local(2026, 8, 19, 23, 59);
    expect(activityLabel(at.toISOString(), now)).toBe("Today, 23:59");
  });

  it("renders nothing rather than Invalid Date for a broken stamp", () => {
    expect(activityLabel("not a date", now)).toBe("");
  });
});

describe("startedAt", () => {
  const now = local(2026, 8, 19, 16, 30);

  it("drops the clock entirely — a thread's start is a day, not a moment", () => {
    expect(startedAt(local(2026, 8, 19, 8, 5).toISOString(), now)).toBe("started today");
    expect(startedAt(local(2026, 8, 18, 8, 5).toISOString(), now)).toBe("started yesterday");
    expect(startedAt(local(2026, 8, 2, 8, 5).toISOString(), now)).toBe("started 2 Aug 2026");
  });

  it("renders nothing for a broken stamp", () => {
    expect(startedAt("", now)).toBe("");
  });
});

describe("conversationCount", () => {
  it("spells out the empty case", () => {
    expect(conversationCount(0)).toBe("No conversations yet");
    expect(conversationCount(1)).toBe("1 conversation");
    expect(conversationCount(5)).toBe("5 conversations");
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

describe("startedDay", () => {
  const now = local(2026, 8, 19, 16, 30);

  it("is a day, never a clock", () => {
    expect(startedDay(local(2026, 8, 19, 8, 5).toISOString(), now)).toBe("Today");
    expect(startedDay(local(2026, 8, 18, 23, 59).toISOString(), now)).toBe("Yesterday");
    expect(startedDay(local(2026, 8, 16, 8, 5).toISOString(), now)).toBe("16 Aug 2026");
    expect(startedDay("nope", now)).toBe("");
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
