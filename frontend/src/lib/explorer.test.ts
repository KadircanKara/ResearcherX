import { describe, expect, it } from "vitest";
import {
  exchangesLabel,
  formatActivity,
  formatDate,
  formatDistance,
  listSummary,
  outcomeLabel,
  startedLabel,
  threadOutcome,
} from "./explorer";

const NOW = "2026-08-19T16:40";

describe("formatActivity", () => {
  it("names today and keeps the clock", () => {
    expect(formatActivity("2026-08-19T15:10", NOW)).toBe("Today, 15:10");
  });

  it("names yesterday and keeps the clock", () => {
    expect(formatActivity("2026-08-18T10:22", NOW)).toBe("Yesterday, 10:22");
  });

  it("drops the clock once the day no longer locates it", () => {
    expect(formatActivity("2026-08-16T09:00", NOW)).toBe("16 Aug 2026");
  });

  it("crosses a month boundary without a timezone", () => {
    expect(formatActivity("2026-07-31T23:59", "2026-08-01T00:30")).toBe(
      "Yesterday, 23:59"
    );
  });

  it("crosses a year boundary", () => {
    expect(formatActivity("2025-12-31T23:00", "2026-01-01T08:00")).toBe(
      "Yesterday, 23:00"
    );
  });

  it("handles a stamp with no time at all", () => {
    expect(formatActivity("2026-08-19", NOW)).toBe("Today");
    expect(formatActivity("2026-08-02", NOW)).toBe("2 Aug 2026");
  });

  // The whole reason dates are strings here: this must not depend on the
  // process timezone, or the server render and the hydration disagree.
  it("does not depend on the host timezone", () => {
    const before = process.env.TZ;
    try {
      process.env.TZ = "Pacific/Kiritimati";
      const a = formatActivity("2026-08-19T15:10", NOW);
      process.env.TZ = "Pacific/Midway";
      const b = formatActivity("2026-08-19T15:10", NOW);
      expect(a).toBe(b);
      expect(a).toBe("Today, 15:10");
    } finally {
      process.env.TZ = before;
    }
  });
});

describe("formatDate", () => {
  it("renders a locale-free day", () => {
    expect(formatDate("2026-08-02T09:00")).toBe("2 Aug 2026");
  });

  it("leaves an unparseable month alone rather than inventing one", () => {
    expect(formatDate("2026-13-02")).toBe("2026-13-02");
  });
});

describe("startedLabel", () => {
  it("reads as a sentence fragment", () => {
    expect(startedLabel("2026-08-19T09:00", NOW)).toBe("today");
    expect(startedLabel("2026-08-18T09:00", NOW)).toBe("yesterday");
    expect(startedLabel("2026-08-11T09:00", NOW)).toBe("11 Aug 2026");
  });
});

describe("outcomeLabel", () => {
  it("spells out the zero case rather than counting to it", () => {
    expect(outcomeLabel(0)).toBe("nothing added");
  });

  it("singularises one", () => {
    expect(outcomeLabel(1)).toBe("1 paper added");
  });

  it("pluralises the rest", () => {
    expect(outcomeLabel(3)).toBe("3 papers added");
  });
});

describe("exchangesLabel", () => {
  it("singularises one", () => {
    expect(exchangesLabel(1)).toBe("1 exchange");
    expect(exchangesLabel(5)).toBe("5 exchanges");
  });
});

describe("listSummary", () => {
  it("totals what every exploration added", () => {
    expect(
      listSummary([{ added: 1 }, { added: 2 }, { added: 0 }, { added: 3 }, { added: 1 }])
    ).toBe("5 explorations · 7 papers added in total");
  });

  it("singularises both halves", () => {
    expect(listSummary([{ added: 1 }])).toBe("1 exploration · 1 paper added in total");
  });

  it("survives an empty library of explorations", () => {
    expect(listSummary([])).toBe("0 explorations · 0 papers added in total");
  });
});

describe("threadOutcome", () => {
  it("pairs the outcome with what was looked at", () => {
    expect(threadOutcome(1, 6)).toBe("1 paper added · 6 considered");
    expect(threadOutcome(0, 4)).toBe("nothing added · 4 considered");
  });
});

describe("formatDistance", () => {
  it("keeps two decimals, so the column stays a column", () => {
    expect(formatDistance(0.58)).toBe("0.58");
    expect(formatDistance(0.6)).toBe("0.60");
  });

  it("renders a paper already held as an em dash, never as zero", () => {
    expect(formatDistance(null)).toBe("—");
  });
});
