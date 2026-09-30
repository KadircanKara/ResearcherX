import { describe, expect, it } from "vitest";
import {
  SENT_NOTICE,
  isValidCode,
  isValidEmail,
  loginErrorMessage,
  normalizeCode,
  normalizeEmail,
  resendWaitSeconds,
} from "./login-flow";

describe("login flow", () => {
  it("normalizes and validates the email", () => {
    expect(normalizeEmail("  Client@Acme.COM ")).toBe("client@acme.com");
    expect(isValidEmail("a@b.co")).toBe(true);
    expect(isValidEmail("a@b")).toBe(false);
    expect(isValidEmail("")).toBe(false);
  });
  it("accepts a pasted code with spaces", () => {
    expect(normalizeCode(" 123 456 ")).toBe("123456");
    expect(isValidCode("123456")).toBe(true);
    expect(isValidCode("12345")).toBe(false);
    expect(isValidCode("12a456")).toBe(false);
  });
  it("counts down the resend window", () => {
    expect(resendWaitSeconds(0, 0)).toBe(60);
    expect(resendWaitSeconds(0, 59_001)).toBe(1);
    expect(resendWaitSeconds(0, 60_000)).toBe(0);
  });
  it("never reveals whether an email is invited", () => {
    expect(SENT_NOTICE).toBe("If this email has access, a code is on its way.");
  });
  it("maps Supabase errors to fixed text", () => {
    expect(loginErrorMessage({ status: 429 })).toBe(
      "Too many attempts. Wait a minute and try again."
    );
    expect(loginErrorMessage({ status: 403, code: "otp_expired" })).toBe(
      "That code is wrong or has expired. Request a new one."
    );
    expect(loginErrorMessage(null)).toBe("Sign-in failed. Try again.");
  });
});
