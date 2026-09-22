import { describe, expect, it } from "vitest";
import { initialSizes, resizeAt } from "./resizable";

const free = { min: 0, max: 100 };

describe("initialSizes", () => {
  it("keeps every size that was given", () => {
    expect(initialSizes([20, 80])).toEqual([20, 80]);
  });

  it("splits the remainder evenly between panels without a default", () => {
    expect(initialSizes([20, undefined])).toEqual([20, 80]);
    expect(initialSizes([undefined, undefined, undefined])).toEqual([100 / 3, 100 / 3, 100 / 3]);
  });

  it("never hands a bare panel a negative share", () => {
    expect(initialSizes([70, 50, undefined])).toEqual([70, 50, 0]);
  });
});

describe("resizeAt", () => {
  it("moves one boundary and keeps the pair's total", () => {
    expect(resizeAt([20, 80], 0, 5, [free, free])).toEqual([25, 75]);
    expect(resizeAt([20, 80], 0, -5, [free, free])).toEqual([15, 85]);
  });

  it("clamps to the first panel's own min and max", () => {
    const rail = { min: 14, max: 32 };
    expect(resizeAt([20, 80], 0, 40, [rail, free])).toEqual([32, 68]);
    expect(resizeAt([20, 80], 0, -40, [rail, free])).toEqual([14, 86]);
  });

  it("clamps to the second panel's min and max too", () => {
    expect(resizeAt([50, 50], 0, 40, [free, { min: 30, max: 100 }])).toEqual([70, 30]);
    expect(resizeAt([50, 50], 0, -40, [free, { min: 0, max: 60 }])).toEqual([40, 60]);
  });

  it("leaves panels outside the pair alone", () => {
    expect(resizeAt([20, 40, 40], 1, 10, [free, free, free])).toEqual([20, 50, 30]);
  });

  it("returns the sizes unchanged for a handle that does not exist", () => {
    expect(resizeAt([20, 80], 1, 10, [free, free])).toEqual([20, 80]);
    expect(resizeAt([20, 80], -1, 10, [free, free])).toEqual([20, 80]);
  });

  it("does not move a pair whose constraints cannot both hold", () => {
    expect(resizeAt([50, 50], 0, 10, [{ min: 70, max: 100 }, { min: 40, max: 100 }])).toEqual([
      50, 50,
    ]);
  });
});
