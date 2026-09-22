import { describe, expect, it, vi } from "vitest";
import { publishProjectColor, subscribeProjectColor } from "./project-store";

describe("project colour store", () => {
  it("delivers a change to every live subscriber", () => {
    const a = vi.fn();
    const b = vi.fn();
    const offA = subscribeProjectColor(a);
    const offB = subscribeProjectColor(b);

    publishProjectColor({ id: "p1", color: "#EF4444" });

    expect(a).toHaveBeenCalledWith({ id: "p1", color: "#EF4444" });
    expect(b).toHaveBeenCalledWith({ id: "p1", color: "#EF4444" });
    offA();
    offB();
  });

  it("stops delivering after unsubscribe", () => {
    const fn = vi.fn();
    const off = subscribeProjectColor(fn);
    off();

    publishProjectColor({ id: "p1", color: "#EF4444" });

    expect(fn).not.toHaveBeenCalled();
  });

  it("does not replay past changes to a late subscriber", () => {
    publishProjectColor({ id: "p1", color: "#EF4444" });
    const fn = vi.fn();
    const off = subscribeProjectColor(fn);

    expect(fn).not.toHaveBeenCalled();
    off();
  });
});

describe("project list change signal", () => {
  it("notifies every live subscriber and stops after unsubscribe", async () => {
    const { publishProjectListChanged, subscribeProjectListChanged } = await import("./project-store");
    const a = vi.fn();
    const b = vi.fn();
    const offA = subscribeProjectListChanged(a);
    const offB = subscribeProjectListChanged(b);
    publishProjectListChanged();
    offA();
    publishProjectListChanged();
    offB();
    publishProjectListChanged();
    expect(a).toHaveBeenCalledTimes(1);
    expect(b).toHaveBeenCalledTimes(2);
  });
});
