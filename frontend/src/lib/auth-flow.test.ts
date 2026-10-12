import { describe, expect, it } from "vitest";
import {
  PASSWORD_MIN,
  RESET_NOTICE,
  SIGNUP_NOTICE,
  authErrorMessage,
  parseEmailLink,
  passwordProblem,
} from "./auth-flow";

describe("passwordProblem", () => {
  it("requires the minimum length", () => {
    expect(passwordProblem("a".repeat(PASSWORD_MIN - 1))).toMatch(/at least/);
    expect(passwordProblem("a".repeat(PASSWORD_MIN))).toBeNull();
  });
  it("counts bcrypt's 72-byte ceiling in BYTES, not characters", () => {
    expect(passwordProblem("a".repeat(72))).toBeNull();
    expect(passwordProblem("a".repeat(73))).toMatch(/at most/);
    // 30 three-byte characters: 30 chars, 90 bytes.
    expect(passwordProblem("€".repeat(30))).toMatch(/at most/);
  });
  it("refuses the email as the password", () => {
    expect(passwordProblem("Client@Acme.com", "client@acme.com")).toMatch(/email/);
  });
});

describe("authErrorMessage", () => {
  it("maps known codes to fixed text", () => {
    expect(authErrorMessage({ code: "invalid_credentials", status: 400 })).toBe(
      "Email or password is incorrect.",
    );
    expect(authErrorMessage({ code: "email_not_confirmed" })).toMatch(/Activate/);
    expect(authErrorMessage({ code: "otp_expired", status: 403 })).toMatch(/expired/);
  });
  it("treats every rate limit alike", () => {
    expect(authErrorMessage({ status: 429 })).toMatch(/Too many/);
    expect(authErrorMessage({ code: "over_email_send_rate_limit" })).toMatch(/Too many/);
  });
  it("falls back to a generic message, never Supabase's own text", () => {
    expect(authErrorMessage({ code: "unexpected_failure" })).toBe(
      "Something went wrong. Try again.",
    );
    expect(authErrorMessage(null)).toBe("Something went wrong. Try again.");
  });
});

describe("parseEmailLink", () => {
  const hash = "pkce_0123456789abcdef";
  it("accepts an activation link on the confirm page", () => {
    expect(parseEmailLink(`?token_hash=${hash}&type=email`, "confirm")).toEqual({
      tokenHash: hash,
      type: "email",
    });
    expect(parseEmailLink(`token_hash=${hash}&type=signup`, "confirm")?.type).toBe("signup");
  });
  it("keeps recovery links on the reset page and nowhere else", () => {
    expect(parseEmailLink(`?token_hash=${hash}&type=recovery`, "confirm")).toBeNull();
    expect(parseEmailLink(`?token_hash=${hash}&type=recovery`, "recovery")?.type).toBe(
      "recovery",
    );
    expect(parseEmailLink(`?token_hash=${hash}&type=email`, "recovery")).toBeNull();
  });
  it("refuses a missing or malformed token", () => {
    expect(parseEmailLink("?type=email", "confirm")).toBeNull();
    expect(parseEmailLink("?token_hash=a%20b&type=email", "confirm")).toBeNull();
  });
});

describe("notices", () => {
  it("never reveal whether an email has an account", () => {
    for (const notice of [SIGNUP_NOTICE, RESET_NOTICE]) {
      expect(notice).not.toMatch(/already|exists|registered|no account/i);
    }
  });
});
