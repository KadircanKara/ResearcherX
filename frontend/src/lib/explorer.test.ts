import { describe, expect, it } from "vitest";
import {
  DISTANCE_ADJACENT,
  DISTANCE_CLOSE,
  REPLAY_MIN_DURATION_MS,
  REPLAY_STAGE_DELAYS_MS,
  REPLAY_STAGE_DONE,
  REPLAY_STREAM_START_MS,
  REPLAY_WORD_MS,
  clockLabel,
  distanceLabel,
  distanceMeterPercent,
  formatActivity,
  formatDistance,
  formatStarted,
  insertMention,
  mentionOpen,
  monthDay,
  outcomeLabel,
  replayLabel,
  replaySchedule,
  reviewLabel,
  severityClass,
  trailSummary,
} from "./explorer";
import { MOCK_NOW, explorationThreads, explorations } from "./explorer-data";

describe("distanceLabel", () => {
  it("calls an absent distance in-library rather than zero", () => {
    expect(distanceLabel(undefined)).toBe("in library");
    // A real 0 is NOT the same thing: it is a distance, and a close one.
    expect(distanceLabel(0)).toBe("close to your library");
  });

  it("puts anything under the close threshold close", () => {
    expect(distanceLabel(0.31)).toBe("close to your library");
    expect(distanceLabel(0.399)).toBe("close to your library");
  });

  it("treats the close threshold itself as adjacent", () => {
    expect(distanceLabel(DISTANCE_CLOSE)).toBe("adjacent");
  });

  it("treats the adjacent threshold itself as adjacent, not far", () => {
    expect(distanceLabel(DISTANCE_ADJACENT)).toBe("adjacent");
    expect(distanceLabel(0.58)).toBe("adjacent");
  });

  it("puts anything past the adjacent threshold far", () => {
    expect(distanceLabel(0.701)).toBe("far");
    expect(distanceLabel(0.82)).toBe("far");
  });
});

describe("distanceMeterPercent", () => {
  it("reads as closeness, so it inverts the distance", () => {
    expect(distanceMeterPercent(0.3)).toBeCloseTo(70);
    expect(distanceMeterPercent(0.82)).toBeCloseTo(18);
  });

  it("fills completely for a paper already in the library", () => {
    expect(distanceMeterPercent(undefined)).toBe(100);
  });

  it("clamps rather than drawing a bar wider than its track", () => {
    expect(distanceMeterPercent(-1)).toBe(100);
    expect(distanceMeterPercent(2)).toBe(0);
  });
});

describe("formatDistance", () => {
  it("shows an em dash, not a zero, for a paper in the library", () => {
    expect(formatDistance(undefined)).toBe("—");
    expect(formatDistance(0)).toBe("0.00");
  });

  it("keeps two decimals", () => {
    expect(formatDistance(0.3)).toBe("0.30");
  });
});

describe("reviewLabel", () => {
  it("passes through a pass", () => {
    expect(reviewLabel("pass")).toBe("pass");
  });

  it("counts issues and singularizes one", () => {
    expect(
      reviewLabel([{ claim: "c", severity: "low", note: "n" }])
    ).toBe("1 issue");
    expect(
      reviewLabel([
        { claim: "a", severity: "low", note: "n" },
        { claim: "b", severity: "high", note: "n" },
      ])
    ).toBe("2 issues");
  });
});

describe("trailSummary", () => {
  it("reports plan, search and review in one line", () => {
    expect(
      trailSummary({ queries: [1, 2, 3], review: "pass" })
    ).toBe("Planned 3 queries · searched 3 · reviewed: pass");
  });

  it("carries the issue count through", () => {
    expect(
      trailSummary({
        queries: [1, 2, 3],
        review: [
          { claim: "a", severity: "medium", note: "n" },
          { claim: "b", severity: "low", note: "n" },
        ],
      })
    ).toBe("Planned 3 queries · searched 3 · reviewed: 2 issues");
  });

  it("describes every turn in the corpus", () => {
    for (const thread of explorationThreads) {
      for (const turn of thread.turns) {
        expect(trailSummary(turn)).toMatch(
          /^Planned \d+ queries · searched \d+ · reviewed: (pass|\d+ issues?)$/
        );
      }
    }
  });
});

describe("severityClass", () => {
  it("reserves the loud colours for medium and high", () => {
    expect(severityClass("high")).toBe("text-destructive");
    expect(severityClass("medium")).toBe("text-warning");
    expect(severityClass("low")).toBe("text-muted-foreground");
  });
});

describe("replaySchedule", () => {
  const answer = "One two three four five.";

  it("opens with one frame per pipeline stage", () => {
    const stages = replaySchedule(answer).filter((f) => f.stage !== undefined);
    expect(stages.map((f) => f.stage)).toEqual([1, 2, 3, 4, 5, REPLAY_STAGE_DONE]);
    expect(stages.slice(0, 5).map((f) => f.at)).toEqual([
      ...REPLAY_STAGE_DELAYS_MS,
    ]);
  });

  it("streams one word per frame, cumulatively", () => {
    const words = replaySchedule(answer).filter((f) => f.text !== undefined);
    expect(words.map((f) => f.text)).toEqual([
      "One",
      "One two",
      "One two three",
      "One two three four",
      "One two three four five.",
    ]);
    expect(words[0].at).toBe(REPLAY_STREAM_START_MS);
    expect(words[1].at).toBe(REPLAY_STREAM_START_MS + REPLAY_WORD_MS);
  });

  it("is ordered, so a component can schedule it as it stands", () => {
    const frames = replaySchedule(answer);
    const times = frames.map((f) => f.at);
    expect([...times].sort((a, b) => a - b)).toEqual(times);
  });

  it("never ends before the minimum duration", () => {
    const end = replaySchedule("Short.").at(-1);
    expect(end?.at).toBe(REPLAY_MIN_DURATION_MS);
    expect(end?.stage).toBe(REPLAY_STAGE_DONE);
  });

  it("waits for a long answer instead of cutting it off", () => {
    const long = Array.from({ length: 300 }, (_, i) => `w${i}`).join(" ");
    const end = replaySchedule(long).at(-1);
    const lastWord = replaySchedule(long)
      .filter((f) => f.text !== undefined)
      .at(-1);
    expect(end!.at).toBeGreaterThan(REPLAY_MIN_DURATION_MS);
    expect(end!.at).toBeGreaterThan(lastWord!.at);
  });

  it("still ends when there is nothing to stream", () => {
    const frames = replaySchedule("");
    expect(frames.some((f) => f.text !== undefined)).toBe(false);
    expect(frames.at(-1)).toEqual({
      at: REPLAY_MIN_DURATION_MS,
      stage: REPLAY_STAGE_DONE,
    });
  });
});

describe("replayLabel", () => {
  it("names each stage of a three-query run", () => {
    expect(replayLabel(0, 3)).toBe("Planning…");
    expect(replayLabel(1, 3)).toBe("Searching… 1 of 3");
    expect(replayLabel(3, 3)).toBe("Searching… 3 of 3");
    expect(replayLabel(4, 3)).toBe("Writing…");
    expect(replayLabel(5, 3)).toBe("Reviewing…");
  });

  it("goes quiet once the turn is done, so the summary takes over", () => {
    expect(replayLabel(REPLAY_STAGE_DONE, 3)).toBeNull();
  });
});

describe("clockLabel", () => {
  it("renders a twelve-hour clock without a locale", () => {
    expect(clockLabel("2026-09-22T09:42")).toBe("9:42 AM");
    expect(clockLabel("2026-09-21T16:18")).toBe("4:18 PM");
  });

  it("names midnight and noon correctly", () => {
    expect(clockLabel("2026-09-22T00:05")).toBe("12:05 AM");
    expect(clockLabel("2026-09-22T12:00")).toBe("12:00 PM");
  });

  it("is empty for a stamp carrying no time", () => {
    expect(clockLabel("2026-09-22")).toBe("");
  });
});

describe("monthDay", () => {
  it("drops the year while it matches", () => {
    expect(monthDay("2026-09-18T14:20", MOCK_NOW)).toBe("Sep 18");
  });

  it("keeps the year once it differs", () => {
    expect(monthDay("2025-09-18T14:20", MOCK_NOW)).toBe("Sep 18, 2025");
  });
});

describe("formatStarted", () => {
  it("keeps the clock for today", () => {
    expect(formatStarted("2026-09-22T09:42", MOCK_NOW)).toBe("Today, 9:42 AM");
  });

  it("drops the clock from yesterday onward", () => {
    expect(formatStarted("2026-09-21T11:05", MOCK_NOW)).toBe("Yesterday");
    expect(formatStarted("2026-09-18T14:20", MOCK_NOW)).toBe("Sep 18");
  });

  it("crosses a month boundary without a timezone", () => {
    expect(formatStarted("2026-07-31T23:59", "2026-08-01T00:30")).toBe(
      "Yesterday"
    );
  });

  it("crosses a year boundary", () => {
    expect(formatStarted("2025-12-31T23:00", "2026-01-01T08:00")).toBe(
      "Yesterday"
    );
  });
});

describe("formatActivity", () => {
  it("counts the last hour in minutes", () => {
    expect(formatActivity("2026-09-22T16:28", MOCK_NOW)).toBe("12 min ago");
    expect(formatActivity("2026-09-22T15:41", MOCK_NOW)).toBe("59 min ago");
  });

  it("collapses the first minute to 'just now'", () => {
    expect(formatActivity("2026-09-22T16:40", MOCK_NOW)).toBe("just now");
    expect(formatActivity("2026-09-22T16:39", MOCK_NOW)).toBe("just now");
  });

  it("switches to a wall clock past the hour", () => {
    expect(formatActivity("2026-09-22T15:40", MOCK_NOW)).toBe("Today, 3:40 PM");
  });

  it("keeps the clock on yesterday", () => {
    expect(formatActivity("2026-09-21T16:18", MOCK_NOW)).toBe(
      "Yesterday, 4:18 PM"
    );
  });

  it("drops to a date beyond yesterday", () => {
    expect(formatActivity("2026-09-19T10:05", MOCK_NOW)).toBe("Sep 19");
  });

  it("does not render a negative age for a stamp in the future", () => {
    expect(formatActivity("2026-09-22T16:50", MOCK_NOW)).toBe("Today, 4:50 PM");
  });
});

describe("mentionOpen", () => {
  it("opens on a trailing at-sign", () => {
    expect(mentionOpen("@")).toBe(true);
    expect(mentionOpen("Compare against @")).toBe(true);
    expect(mentionOpen("Compare against\n@")).toBe(true);
  });

  it("stays shut mid-word, so an email address is not a paper picker", () => {
    expect(mentionOpen("ada@")).toBe(false);
    expect(mentionOpen("ada@researcherx.dev")).toBe(false);
  });

  it("stays shut when the at-sign is not the last character", () => {
    expect(mentionOpen("@Swarm ")).toBe(false);
    expect(mentionOpen("")).toBe(false);
  });
});

describe("insertMention", () => {
  it("replaces the trailing at-sign with the whole title", () => {
    expect(insertMention("Compare against @", "Local Learning")).toBe(
      "Compare against @Local Learning "
    );
  });

  it("works on a draft that is nothing but the at-sign", () => {
    expect(insertMention("@", "Swarm Replanning")).toBe("@Swarm Replanning ");
  });
});

describe("outcomeLabel", () => {
  it("spells out an exploration that added nothing", () => {
    expect(outcomeLabel({ added: 0, exchanges: 2, considered: 9 })).toBe(
      "nothing added · 2 exchanges · 9 considered"
    );
  });

  it("singularizes one paper and one exchange", () => {
    expect(outcomeLabel({ added: 1, exchanges: 1, considered: 4 })).toBe(
      "1 paper added · 1 exchange · 4 considered"
    );
  });

  it("describes every row the list renders", () => {
    for (const row of explorations) {
      expect(outcomeLabel(row)).toMatch(/ · \d+ exchanges? · \d+ considered$/);
    }
  });
});

describe("the mock corpus", () => {
  it("dates every thread at or before the clock it is written against", () => {
    for (const thread of explorationThreads) {
      expect(thread.started <= thread.activity).toBe(true);
      expect(thread.activity <= MOCK_NOW).toBe(true);
    }
  });

  it("gives every thread a stable id", () => {
    const ids = explorationThreads.map((t) => t.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("gives every candidate a stable id within its turn", () => {
    for (const thread of explorationThreads) {
      for (const turn of thread.turns) {
        const ids = turn.candidates.map((c) => c.id);
        expect(new Set(ids).size).toBe(ids.length);
      }
    }
  });

  it("leaves a library candidate without a distance", () => {
    for (const thread of explorationThreads) {
      for (const turn of thread.turns) {
        for (const candidate of turn.candidates) {
          if (candidate.status === "library") {
            expect(candidate.distance).toBeUndefined();
          } else {
            expect(typeof candidate.distance).toBe("number");
          }
        }
      }
    }
  });

  it("gives a revised query to exactly the retried searches", () => {
    for (const thread of explorationThreads) {
      for (const turn of thread.turns) {
        for (const query of turn.queries) {
          expect(query.revisedQuery !== undefined).toBe(
            query.status === "retried once"
          );
        }
      }
    }
  });
});
