import { describe, expect, it } from "vitest";
import { ApiError } from "./api-error";
import { limitMessage, papersLine, turnsLeftLine, type Usage } from "./usage";

const usage = (papers: [number, number], turns: [number, number]): Usage => ({
  papers: { used: papers[0], limit: papers[1] },
  chat_turns_today: { used: turns[0], limit: turns[1], resets_at: "2026-10-01T00:00:00Z" },
});

describe("usage lines", () => {
  it("shows the paper count only when there is a limit", () => {
    expect(papersLine(usage([12, 20], [0, 0]))).toBe("12 / 20 papers");
    expect(papersLine(usage([12, 0], [0, 0]))).toBeNull();
  });
  it("shows turns left only when fewer than 20 remain", () => {
    expect(turnsLeftLine(usage([0, 0], [80, 100]))).toBeNull();
    expect(turnsLeftLine(usage([0, 0], [81, 100]))).toBe("19 turns left today");
    expect(turnsLeftLine(usage([0, 0], [99, 100]))).toBe("1 turn left today");
    expect(turnsLeftLine(usage([0, 0], [100, 100]))).toBe("0 turns left today");
    expect(turnsLeftLine(usage([0, 0], [5, 0]))).toBeNull();
  });
});

describe("limitMessage", () => {
  it("passes a 429's server text through and ignores everything else", () => {
    expect(limitMessage(new ApiError(429, "Paper limit reached (20). Delete a paper to add another.")))
      .toBe("Paper limit reached (20). Delete a paper to add another.");
    expect(limitMessage(new ApiError(500, "boom"))).toBeNull();
    expect(limitMessage(new Error("x"))).toBeNull();
  });
});
