/**
 * The rules behind /login, PURE for vitest's node environment.
 *
 * Sign-in is two steps: email, then the 6-digit code Supabase mails. The
 * page never says whether an email is invited — `SENT_NOTICE` reads the same
 * either way, so the form cannot be used to probe who has access.
 */
export const SENT_NOTICE = "If this email has access, a code is on its way.";

export const normalizeEmail = (s: string) => s.trim().toLowerCase();
export const isValidEmail = (s: string) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(s);
export const normalizeCode = (s: string) => s.replace(/\s+/g, "");
export const isValidCode = (s: string) => /^\d{6}$/.test(s);

/** Seconds left before Supabase will send another code (its 60s window). */
export function resendWaitSeconds(sentAtMs: number, nowMs: number, windowS = 60): number {
  return Math.max(0, Math.ceil(windowS - (nowMs - sentAtMs) / 1000));
}

export function loginErrorMessage(err: { status?: number; code?: string } | null): string {
  if (err?.status === 429) return "Too many attempts. Wait a minute and try again.";
  if (err?.code === "otp_expired" || err?.status === 403) {
    return "That code is wrong or has expired. Request a new one.";
  }
  return "Sign-in failed. Try again.";
}
