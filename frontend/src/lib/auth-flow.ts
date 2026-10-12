/**
 * The rules behind sign-up, password sign-in, activation and password reset,
 * PURE for vitest's node environment (the pages are thin shells over this).
 *
 * Two properties every message here keeps:
 * - It never says whether an email has an account. Supabase already answers
 *   a sign-up for a registered address with a success that sends nothing, and
 *   a reset for an unknown one the same way; the copy must not undo that.
 * - It never shows Supabase's own error text, which is written for
 *   developers and occasionally names internals. Codes map to fixed strings.
 */
import { normalizeEmail } from "./login-flow";

/** Long enough to resist guessing without a composition rule. Mirror the
 * dashboard's minimum (RUNBOOK section 4); the server is the enforcement. */
export const PASSWORD_MIN = 10;
/** bcrypt, which Supabase hashes with, silently ignores bytes past 72. */
export const PASSWORD_MAX_BYTES = 72;

export const SIGNUP_NOTICE =
  "Check your email for a link to activate your account. It expires in one hour.";
export const RESET_NOTICE = "If an account uses this email, a reset link is on its way.";

export function passwordProblem(password: string, email = ""): string | null {
  if (password.length < PASSWORD_MIN) return `Use at least ${PASSWORD_MIN} characters.`;
  if (new TextEncoder().encode(password).length > PASSWORD_MAX_BYTES) {
    return `Use at most ${PASSWORD_MAX_BYTES} bytes (shorter, or fewer special characters).`;
  }
  if (email && password.trim().toLowerCase() === normalizeEmail(email)) {
    return "Don't use your email address as your password.";
  }
  return null;
}

export type AuthErrorLike = { status?: number; code?: string } | null | undefined;

export function authErrorMessage(err: AuthErrorLike): string {
  const code = err?.code ?? "";
  if (err?.status === 429 || code.startsWith("over_")) {
    return "Too many attempts. Wait a minute and try again.";
  }
  switch (code) {
    case "invalid_credentials":
      return "Email or password is incorrect.";
    case "email_not_confirmed":
      return "Activate your account first: use the link we emailed you.";
    case "weak_password":
      return "Choose a stronger password: longer, and not a common one.";
    case "same_password":
      return "Choose a password different from your current one.";
    case "captcha_failed":
      return "The verification check failed. Complete it and try again.";
    case "otp_expired":
    case "flow_state_expired":
    case "bad_code_verifier":
      return "This link has expired or was already used. Request a new one.";
    case "signup_disabled":
      return "Sign-up is closed right now.";
    case "user_banned":
      return "This account is disabled.";
  }
  return "Something went wrong. Try again.";
}

/** The two email-link kinds the app handles, keyed by landing page. */
export type ConfirmType = "email" | "signup" | "email_change";
export type LinkType = ConfirmType | "recovery";

const CONFIRM_TYPES: readonly string[] = ["email", "signup", "email_change"];

/**
 * Read `?token_hash=…&type=…` from an email link. Returns null for anything
 * else, including a type that belongs on the OTHER landing page: a recovery
 * link opened on /auth/confirm must not sign the user in there, where nothing
 * then asks them for a new password.
 */
export function parseEmailLink(
  search: string,
  expect: "confirm" | "recovery",
): { tokenHash: string; type: LinkType } | null {
  const params = new URLSearchParams(search);
  const tokenHash = params.get("token_hash") ?? "";
  const type = params.get("type") ?? "";
  if (!/^[A-Za-z0-9_-]{8,}$/.test(tokenHash)) return null;
  if (expect === "recovery") return type === "recovery" ? { tokenHash, type } : null;
  return CONFIRM_TYPES.includes(type) ? { tokenHash, type: type as ConfirmType } : null;
}
