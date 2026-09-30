import { describe, expect, it } from "vitest";
import { buildAuthHeaders, isSessionEnded } from "./auth-headers";

describe("buildAuthHeaders", () => {
  it("sends the bearer token when there is a session", () => {
    expect(buildAuthHeaders({ devUserId: "u1", accessToken: "tok" })).toEqual({
      Authorization: "Bearer tok",
    });
  });
  it("falls back to the dev header without a session", () => {
    expect(buildAuthHeaders({ devUserId: "u1", accessToken: null })).toEqual({
      "X-Dev-User-Id": "u1",
    });
  });
  it("sends nothing when neither is set", () => {
    expect(buildAuthHeaders({ devUserId: null, accessToken: null })).toEqual({});
  });
});

describe("isSessionEnded", () => {
  it("only a 401 under real auth ends the session", () => {
    expect(isSessionEnded(401, true)).toBe(true);
    expect(isSessionEnded(503, true)).toBe(false); // keys unreachable: keep the session
    expect(isSessionEnded(403, true)).toBe(false);
    expect(isSessionEnded(401, false)).toBe(false); // dev seam never signs out
  });
});
