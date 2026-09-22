import { describe, expect, it } from "vitest";
import {
  datePart,
  formatDate,
  formatShortDay,
  initials,
  plural,
  previousDay,
  relativeLabel,
  timePart,
} from "./format";

describe("datePart / timePart", () => {
  it("splits a full stamp", () => {
    expect(datePart("2026-08-19T16:40")).toBe("2026-08-19");
    expect(timePart("2026-08-19T16:40")).toBe("16:40");
  });

  it("returns no time for a date-only stamp", () => {
    expect(datePart("2026-08-19")).toBe("2026-08-19");
    expect(timePart("2026-08-19")).toBe("");
  });
});

describe("formatDate", () => {
  it("renders without a locale, so every machine agrees", () => {
    expect(formatDate("2026-08-16")).toBe("16 Aug 2026");
    expect(formatDate("2026-01-02T09:00")).toBe("2 Jan 2026");
  });

  it("degrades to the raw date rather than throwing on a bad month", () => {
    expect(formatDate("2026-13-02")).toBe("2026-13-02");
  });
});

describe("previousDay", () => {
  it("steps back inside a month", () => {
    expect(previousDay("2026-08-19")).toBe("2026-08-18");
  });

  it("crosses a month boundary", () => {
    expect(previousDay("2026-08-01")).toBe("2026-07-31");
  });

  it("crosses a year boundary", () => {
    expect(previousDay("2026-01-01")).toBe("2025-12-31");
  });

  it("handles a leap day", () => {
    expect(previousDay("2028-03-01")).toBe("2028-02-29");
  });
});

describe("plural", () => {
  it("singularizes exactly one", () => {
    expect(plural(1, "paper")).toBe("1 paper");
    expect(plural(0, "paper")).toBe("0 papers");
    expect(plural(2, "paper")).toBe("2 papers");
  });

  it("takes an irregular plural", () => {
    expect(plural(2, "entry", "entries")).toBe("2 entries");
  });
});

describe("formatShortDay", () => {
  it("drops the year", () => {
    expect(formatShortDay("2026-09-18")).toBe("18 Sep");
    expect(formatShortDay("2026-01-02T09:00:00+00:00")).toBe("2 Jan");
  });

  it("degrades to the raw date on a bad month", () => {
    expect(formatShortDay("2026-13-02")).toBe("2026-13-02");
  });
});

describe("relativeLabel", () => {
  const now = new Date("2026-09-22T16:25:00Z");

  it("reads minutes, then hours", () => {
    expect(relativeLabel("2026-09-22T16:24:50Z", now)).toBe("just now");
    expect(relativeLabel("2026-09-22T16:13:00Z", now)).toBe("12 min ago");
    expect(relativeLabel("2026-09-22T14:25:00Z", now)).toBe("2h ago");
  });

  it("says Yesterday inside two days, then the date", () => {
    expect(relativeLabel("2026-09-21T10:00:00Z", now)).toBe("Yesterday");
    expect(relativeLabel("2026-09-18T10:00:00Z", now)).toBe("Sep 18");
  });

  it("returns an unparseable stamp as it came", () => {
    expect(relativeLabel("not a date", now)).toBe("not a date");
  });
});

describe("initials", () => {
  it("takes up to two initials, upper-cased", () => {
    expect(initials("Ada Kim")).toBe("AK");
    expect(initials("ada lovelace byron")).toBe("AL");
    expect(initials("Ada")).toBe("A");
  });

  it("ignores extra whitespace", () => {
    expect(initials("  Ada   Kim ")).toBe("AK");
  });
});
