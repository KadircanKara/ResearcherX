import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SaveEngine } from "./latex-buffers";

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

function engine(send = vi.fn(async (_path: string, _text: string) => {})) {
  const states: string[] = [];
  const e = new SaveEngine({ delayMs: 800, send, onStateChange: (s) => states.push(s) });
  return { e, send, states };
}

describe("debounce", () => {
  it("sends once with the latest text when two keystrokes land inside the window", async () => {
    const { e, send } = engine();
    e.setBaseline("a.tex", "");
    e.schedule("a.tex", "h");
    e.schedule("a.tex", "hi");
    await vi.advanceTimersByTimeAsync(800);
    expect(send).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenCalledWith("a.tex", "hi");
  });

  it("debounces each path on its own timer", async () => {
    const { e, send } = engine();
    e.setBaseline("a.tex", "");
    e.setBaseline("b.tex", "");
    e.schedule("a.tex", "one");
    e.schedule("b.tex", "two");
    await vi.advanceTimersByTimeAsync(800);
    expect(send.mock.calls.map((c) => c[0]).sort()).toEqual(["a.tex", "b.tex"]);
  });

  it("does not send text identical to the server baseline", async () => {
    const { e, send } = engine();
    e.setBaseline("a.tex", "same");
    e.schedule("a.tex", "same");
    await vi.advanceTimersByTimeAsync(800);
    expect(send).not.toHaveBeenCalled();
  });
});

describe("flushAll", () => {
  it("sends every pending path before its timer fires", async () => {
    const { e, send } = engine();
    e.setBaseline("a.tex", "");
    e.setBaseline("b.tex", "");
    e.schedule("a.tex", "1");
    e.schedule("b.tex", "2");
    await e.flushAll();
    expect(send).toHaveBeenCalledTimes(2);
  });

  it("waits for a send that was ALREADY on the wire", async () => {
    // The single-file bug this exists to close: the debounce had already
    // fired, so `pending` was empty and flush returned instantly while the
    // PATCH was still in flight -- and the compile built the previous text.
    let release: () => void = () => {};
    const send = vi.fn(() => new Promise<void>((r) => { release = r; }));
    const { e } = engine(send);
    e.setBaseline("a.tex", "");
    e.schedule("a.tex", "x");
    await vi.advanceTimersByTimeAsync(800);   // send starts, does not settle
    let settled = false;
    const waiting = e.flushAll().then(() => { settled = true; });
    await Promise.resolve();
    expect(settled).toBe(false);
    release();
    await waiting;
    expect(settled).toBe(true);
  });

  it("two concurrent flushes never send the same edit twice", async () => {
    const { e, send } = engine();
    e.setBaseline("a.tex", "");
    e.schedule("a.tex", "x");
    await Promise.all([e.flushAll(), e.flushAll()]);
    expect(send).toHaveBeenCalledTimes(1);
  });
});

describe("forget and rename", () => {
  it("a forgotten path never sends, even though it was pending", async () => {
    const { e, send } = engine();
    e.setBaseline("a.tex", "");
    e.schedule("a.tex", "x");
    e.forget("a.tex");
    await vi.advanceTimersByTimeAsync(800);
    await e.flushAll();
    expect(send).not.toHaveBeenCalled();
  });

  it("a rename carries the pending edit to the new path", async () => {
    const { e, send } = engine();
    e.setBaseline("a.tex", "");
    e.schedule("a.tex", "x");
    e.rename("a.tex", "b.tex");
    await e.flushAll();
    expect(send).toHaveBeenCalledWith("b.tex", "x");
  });
});

describe("dirtiness", () => {
  it("is dirty from the keystroke, not from when the save lands", () => {
    const { e } = engine();
    e.setBaseline("a.tex", "");
    expect(e.isDirty()).toBe(false);
    e.schedule("a.tex", "x");
    expect(e.isDirty()).toBe(true);
  });

  it("stops being dirty once the save succeeds", async () => {
    const { e } = engine();
    e.setBaseline("a.tex", "");
    e.schedule("a.tex", "x");
    await e.flushAll();
    expect(e.isDirty()).toBe(false);
  });

  it("STAYS dirty when the save fails", async () => {
    // The text on screen is not the text on the server. Reporting clean here
    // would let a compile of the OLD server text present itself as matching
    // what the user is looking at.
    const send = vi.fn(async () => { throw new Error("boom"); });
    const { e, states } = engine(send);
    e.setBaseline("a.tex", "");
    e.schedule("a.tex", "x");
    await e.flushAll();
    expect(e.isDirty()).toBe(true);
    expect(states.at(-1)).toBe("error");
  });

  it("recovers when a later save succeeds", async () => {
    let fail = true;
    const send = vi.fn(async () => { if (fail) throw new Error("boom"); });
    const { e } = engine(send);
    e.setBaseline("a.tex", "");
    e.schedule("a.tex", "x");
    await e.flushAll();
    fail = false;
    e.schedule("a.tex", "y");
    await e.flushAll();
    expect(e.isDirty()).toBe(false);
  });

  it("names the paths that are dirty, not just whether any is", async () => {
    const { e } = engine();
    e.setBaseline("a.tex", "");
    e.setBaseline("b.tex", "");
    e.schedule("a.tex", "x");
    expect(e.dirtyPaths()).toEqual(["a.tex"]);
    await e.flushAll();
    expect(e.dirtyPaths()).toEqual([]);
  });
});

describe("dispose", () => {
  it("cancels every outstanding timer", async () => {
    const { e, send } = engine();
    e.setBaseline("a.tex", "");
    e.schedule("a.tex", "x");
    e.dispose();
    await vi.advanceTimersByTimeAsync(800);
    expect(send).not.toHaveBeenCalled();
  });
});
