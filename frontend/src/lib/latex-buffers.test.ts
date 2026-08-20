import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SaveEngine } from "./latex-buffers";

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

// eslint-disable-next-line @typescript-eslint/no-unused-vars -- signature must match SaveEngineOptions.send
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

  it("rename while a send for the old path is in flight re-sends under the new name", async () => {
    // The regression this closes: a send already on the wire for "a.tex"
    // used to be able to write `baseline.set("a.tex", ...)` AFTER the rename,
    // resurrecting a baseline for a path that no longer exists while
    // "b.tex" never received the edit at all -- reported clean, text lost.
    let release: () => void = () => {};
    const send = vi.fn(() => new Promise<void>((r) => { release = r; }));
    const { e } = engine(send);
    e.setBaseline("a.tex", "");
    e.schedule("a.tex", "x");
    await vi.advanceTimersByTimeAsync(800); // send starts under "a.tex", does not settle
    e.rename("a.tex", "b.tex");
    expect(e.isDirty()).toBe(true);
    release();
    await vi.advanceTimersByTimeAsync(800); // the re-queued send under "b.tex" fires
    expect(send).toHaveBeenCalledTimes(2);
    expect(send).toHaveBeenNthCalledWith(2, "b.tex", "x");
  });

  it("forget while a send is in flight leaves no baseline behind for the path", async () => {
    // NOTE: only the FIRST send is held open on a manually-released promise;
    // later calls auto-resolve. Holding every call open (as a single shared
    // `release` closure would) deadlocks `await e.flushAll()` below on the
    // fixed module -- the fix correctly leaves baseline unresurrected, so
    // the second `schedule` genuinely triggers a second network call, and
    // that call has to be able to settle for this test to reach its
    // assertion at all.
    let release: () => void = () => {};
    let calls = 0;
    const send = vi.fn(() => {
      calls += 1;
      if (calls === 1) return new Promise<void>((r) => { release = r; });
      return Promise.resolve();
    });
    const { e } = engine(send);
    e.setBaseline("a.tex", "");
    e.schedule("a.tex", "x");
    await vi.advanceTimersByTimeAsync(800);   // send in flight carrying "x"
    e.forget("a.tex");
    release();
    await vi.advanceTimersByTimeAsync(0);

    // Deliberately NO setBaseline here -- calling it would overwrite the
    // resurrected entry and destroy the discriminator. A file re-created at
    // the same path and typed into again must still SEND: if the stale
    // send put a baseline back, the engine would believe the server already
    // holds "x" and skip the write, losing the edit silently.
    e.schedule("a.tex", "x");
    await e.flushAll();
    expect(send).toHaveBeenCalledTimes(2);
  });

  it("flushAll waits out an edit a rename requeued after its snapshot", async () => {
    // Residual of the rename fix: flushAll snapshots pending+inFlight once,
    // synchronously, before its first await. If a rename lands AFTER that
    // snapshot and requeues the carried in-flight text under a NEW path on a
    // fresh timer, a single-snapshot flushAll would resolve without ever
    // waiting on it -- and compile() calls flushAll() then immediately asks
    // the backend to build, so it would build the pre-edit text.
    const releases: Array<() => void> = [];
    const send = vi.fn(() => new Promise<void>((r) => { releases.push(r); }));
    const { e } = engine(send);
    e.setBaseline("a.tex", "");
    e.schedule("a.tex", "x");
    await vi.advanceTimersByTimeAsync(800); // first send in flight under "a.tex"

    const flushing = e.flushAll();
    e.rename("a.tex", "b.tex"); // requeues "x" under "b.tex" on a fresh timer
    releases[0](); // release the original send

    // The requeued send under "b.tex" is dispatched by flushAll's own next
    // pass, not by a timer -- wait for it to actually be called before
    // releasing it, rather than assuming a fixed number of microtask ticks.
    //
    // BOUNDED on purpose. An unbounded `while (...) await Promise.resolve()`
    // does not fail against a broken module -- it hangs, and under
    // `vi.useFakeTimers()` vitest's own testTimeout is starved too, so the
    // run wedges instead of going red. Bound it so a regression REPORTS
    // itself.
    for (let i = 0; i < 100 && send.mock.calls.length < 2; i += 1) {
      await Promise.resolve();
    }
    if (releases[1]) releases[1]();
    await flushing;

    expect(send).toHaveBeenCalledTimes(2);
    expect(send).toHaveBeenNthCalledWith(2, "b.tex", "x");
    expect(e.isDirty()).toBe(false);
  });

  it("dispose while a send is in flight fires no further state change", async () => {
    let release: () => void = () => {};
    const send = vi.fn(() => new Promise<void>((r) => { release = r; }));
    const { e, states } = engine(send);
    e.setBaseline("a.tex", "");
    e.schedule("a.tex", "x");
    await vi.advanceTimersByTimeAsync(800); // send starts, does not settle
    const countBeforeDispose = states.length;
    e.dispose();
    release();
    await Promise.resolve();
    await Promise.resolve();
    expect(states.length).toBe(countBeforeDispose);
  });

  it("rename carries the failed flag to the new path", async () => {
    const send = vi.fn(async () => { throw new Error("boom"); });
    const { e } = engine(send);
    e.setBaseline("a.tex", "");
    e.schedule("a.tex", "x");
    await e.flushAll();
    expect(e.dirtyPaths()).toEqual(["a.tex"]);
    e.rename("a.tex", "b.tex");
    expect(e.dirtyPaths()).toEqual(["b.tex"]);
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

describe("revert while a save is in flight", () => {
  it("sends the revert rather than trusting a baseline that has not caught up", async () => {
    // The exact sequence this closes: baseline "A", the user types "B", the
    // debounce puts "B" on the wire, and inside that round trip the user
    // undoes back to "A". Against the naive `text === baseline` skip the
    // revert was never sent -- baseline still read "A" because "B" had not
    // resolved -- and "B" then landed as the server's state with the engine
    // reporting perfectly clean. The next compile built "B".
    //
    // The revert is now CHAINED behind "B" rather than racing it (see
    // `flushPath`), so it is dispatched once "B" lands, not alongside it --
    // which is why "B" is released before the second call is expected.
    const releases: Array<() => void> = [];
    const send = vi.fn(() => new Promise<void>((r) => { releases.push(r); }));
    const { e } = engine(send);
    e.setBaseline("a.tex", "A");

    e.schedule("a.tex", "B");
    await vi.advanceTimersByTimeAsync(800); // "B" on the wire, held open
    expect(send).toHaveBeenNthCalledWith(1, "a.tex", "B");

    e.schedule("a.tex", "A"); // the undo, inside "B"'s round trip
    await vi.advanceTimersByTimeAsync(800);
    // Nothing new on the wire yet: one PUT per path at a time is the whole
    // point, and the revert is queued behind "B".
    expect(send).toHaveBeenCalledTimes(1);
    expect(e.isDirty()).toBe(true);

    releases[0]();
    // BOUNDED on purpose -- an unbounded spin does not fail against a broken
    // module, it hangs the suite, because fake timers starve vitest's own
    // testTimeout. See `flushAll waits out an edit a rename requeued` above.
    for (let i = 0; i < 100 && send.mock.calls.length < 2; i += 1) {
      await Promise.resolve();
    }
    expect(send).toHaveBeenNthCalledWith(2, "a.tex", "A");

    for (const release of releases) release();
    for (let i = 0; i < 100 && e.isDirty(); i += 1) {
      await Promise.resolve();
    }
    // The LAST thing the server was told is the text the editor shows.
    expect(send.mock.calls.at(-1)).toEqual(["a.tex", "A"]);
    expect(e.isDirty()).toBe(false);
  });

  it("still skips a send whose text the server already holds", async () => {
    // The don't-resend optimisation is kept, just moved: the comparison now
    // happens after the chain has drained, at the one moment `baseline` is
    // guaranteed to be what the server holds.
    const releases: Array<() => void> = [];
    const send = vi.fn(() => new Promise<void>((r) => { releases.push(r); }));
    const { e } = engine(send);
    e.setBaseline("a.tex", "A");
    e.schedule("a.tex", "B");
    await vi.advanceTimersByTimeAsync(800);
    e.schedule("a.tex", "B"); // same text again, still in flight
    await vi.advanceTimersByTimeAsync(800);
    releases[0]();
    for (let i = 0; i < 100 && e.isDirty(); i += 1) await Promise.resolve();
    expect(send).toHaveBeenCalledTimes(1);
    expect(e.isDirty()).toBe(false);
  });

  it("ends with the server holding the LAST text even when responses resolve out of order", async () => {
    // Two overlapping PUTs to one endpoint is the failure this closes, and
    // response ordering is the part nobody controls. Sequence: baseline
    // "A", type "B", debounce sends "B"; revert to "A", debounce would have
    // sent "A" alongside it. Before the fix both were outstanding, and
    // resolving "A" first and "B" second left `baseline` reading "B" while
    // the editor showed "A", everything empty and `isDirty()` false -- and
    // the file's real contents decided by whichever PUT the server applied
    // last, unknowably.
    //
    // The probe is deliberately hostile: whatever is outstanding is
    // released NEWEST-FIRST, which is exactly the ordering that broke the
    // old code. With sends serialised there is only ever one outstanding,
    // so "newest first" and "in order" are the same thing and the end state
    // is deterministic.
    const calls: Array<{ path: string; text: string; resolve: () => void; done: boolean }> = [];
    const send = vi.fn(
      (path: string, text: string) =>
        new Promise<void>((r) => {
          const entry = { path, text, done: false, resolve: () => { entry.done = true; r(); } };
          calls.push(entry);
        })
    );
    const { e } = engine(send);
    e.setBaseline("a.tex", "A");

    e.schedule("a.tex", "B");
    await vi.advanceTimersByTimeAsync(800);
    e.schedule("a.tex", "A");
    await vi.advanceTimersByTimeAsync(800);

    // BOUNDED, like every other poll in this file: an unbounded loop hangs
    // the suite under fake timers instead of reporting a regression.
    for (let pass = 0; pass < 10; pass += 1) {
      const outstanding = calls.filter((c) => !c.done);
      if (outstanding.length === 0) break;
      outstanding[outstanding.length - 1].resolve(); // newest first
      for (let i = 0; i < 50; i += 1) await Promise.resolve();
    }

    // The last thing the server was told is the text the editor shows, and
    // nothing is left claiming otherwise.
    expect(send.mock.calls.at(-1)).toEqual(["a.tex", "A"]);
    expect(e.isDirty()).toBe(false);
    expect(e.dirtyPaths()).toEqual([]);

    // Re-scheduling the SAME text must now be a no-op: that is only true if
    // `baseline` genuinely records "A". If it recorded "B" -- the old end
    // state -- this would send again, so this assertion is the one that
    // pins which text the engine believes the server holds.
    //
    // Deliberately NOT `await e.flushAll()`: against the broken module that
    // send is never released and the flush never resolves, so the test would
    // HANG on vitest's testTimeout instead of reporting the wrong baseline.
    // A timer advance plus a bounded settle turns the same regression into a
    // plain assertion failure.
    const before = send.mock.calls.length;
    e.schedule("a.tex", "A");
    await vi.advanceTimersByTimeAsync(800);
    for (let i = 0; i < 50; i += 1) await Promise.resolve();
    expect(send.mock.calls.length).toBe(before);
  });
});

describe("chained sends", () => {
  /** A send whose promise the test releases by hand. */
  function held() {
    const calls: Array<{ path: string; text: string; release: () => void; done: boolean }> = [];
    const send = vi.fn(
      (path: string, text: string) =>
        new Promise<void>((resolve, reject) => {
          const entry = {
            path,
            text,
            done: false,
            release: () => {
              entry.done = true;
              resolve();
            },
            fail: () => {
              entry.done = true;
              reject(new Error("nope"));
            },
          };
          calls.push(entry as (typeof calls)[number]);
        })
    );
    return { send, calls };
  }

  /** Bounded microtask settle -- an unbounded loop hangs the suite under
   * fake timers instead of reporting a regression. */
  async function settle() {
    for (let i = 0; i < 50; i += 1) await Promise.resolve();
  }

  it("re-queues an in-flight edit on rename even when a FAILED earlier send carried the same text", async () => {
    // The window: two chained sends for one path carrying byte-identical
    // text. The first FAILS. Clearing the in-flight record by comparing
    // TEXT let the first send's `finally` delete the second's entry while
    // that one was still on the wire -- so `rename` found nothing to
    // re-queue and the user's text reached neither name.
    const { send, calls } = held();
    const { e } = engine(send);
    e.setBaseline("a.tex", "");

    e.schedule("a.tex", "x");
    await vi.advanceTimersByTimeAsync(800); // first send on the wire, holding
    e.schedule("a.tex", "x"); // identical text, chained behind it
    await vi.advanceTimersByTimeAsync(800);

    // Fail the FIRST send. Its `finally` runs while the second is queued.
    (calls[0] as unknown as { fail: () => void }).fail();
    await settle();

    e.rename("a.tex", "b.tex");
    await vi.advanceTimersByTimeAsync(800);
    await settle();

    // The text must be on its way to the NEW name. Without the fix the
    // rename carried nothing and "x" was never written anywhere.
    expect(send.mock.calls.some(([p, t]) => p === "b.tex" && t === "x")).toBe(true);
  });

  it("skips a queued send whose text is no longer the latest, so flushAll waits on at most two", async () => {
    // Without the skip, flushAll drains the whole backlog one round trip at
    // a time: N edits typed while the wire is busy cost N x RTT before a
    // compile can start, every one of them writing text the next
    // immediately replaces.
    const { send, calls } = held();
    const { e } = engine(send);
    e.setBaseline("a.tex", "");

    e.schedule("a.tex", "1");
    await vi.advanceTimersByTimeAsync(800); // "1" on the wire, held open
    for (const text of ["2", "3", "4", "5"]) {
      e.schedule("a.tex", text);
      await vi.advanceTimersByTimeAsync(800);
    }

    const flushed = e.flushAll();
    // Release everything, bounded, oldest first -- the order the chain runs.
    for (let pass = 0; pass < 10; pass += 1) {
      const outstanding = calls.filter((c) => !c.done);
      if (outstanding.length === 0) break;
      outstanding[0].release();
      await vi.advanceTimersByTimeAsync(0);
      await settle();
    }
    await flushed;

    // Two round trips: the one that was already landing, and the latest.
    expect(send.mock.calls.map(([, t]) => t)).toEqual(["1", "5"]);
    expect(e.isDirty()).toBe(false);
  });
});
