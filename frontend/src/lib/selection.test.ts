import { describe, expect, it } from "vitest";
import { clear, isAllSelected, selectAll, toggle } from "./selection";

describe("toggle", () => {
  it("adds an unselected id and removes a selected one", () => {
    const once = toggle(new Set<string>(), "a");
    expect([...once]).toEqual(["a"]);
    expect([...toggle(once, "a")]).toEqual([]);
  });

  it("returns a new set rather than mutating", () => {
    const before = new Set(["a"]);
    const after = toggle(before, "b");
    expect([...before]).toEqual(["a"]);
    expect(after).not.toBe(before);
  });
});

describe("selectAll", () => {
  it("unions the visible ids into the selection", () => {
    expect([...selectAll(new Set(["a"]), ["b", "c"])].sort()).toEqual(["a", "b", "c"]);
  });

  it("only ever means the ids it was handed", () => {
    // The contract is the VISIBLE set. If these lists are ever paginated, a
    // Select all must not silently delete rows the user never saw.
    expect([...selectAll(new Set<string>(), ["a"])]).toEqual(["a"]);
  });
});

describe("isAllSelected", () => {
  it("is true only when every visible id is selected", () => {
    expect(isAllSelected(new Set(["a", "b"]), ["a", "b"])).toBe(true);
    expect(isAllSelected(new Set(["a"]), ["a", "b"])).toBe(false);
  });

  it("is false for an empty list", () => {
    // Otherwise an empty list renders "Clear" as though everything were
    // selected, and the Delete button reads as armed.
    expect(isAllSelected(new Set<string>(), [])).toBe(false);
  });
});

describe("clear", () => {
  it("returns an empty set", () => {
    expect([...clear()]).toEqual([]);
  });
});
