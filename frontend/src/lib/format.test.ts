import { describe, expect, it } from "vitest";
import { datePart, formatDate, plural, previousDay, timePart } from "./format";

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
