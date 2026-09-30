/**
 * Which identity headers a backend request carries. PURE so it is testable
 * under vitest's node environment.
 *
 * A real session wins: under AUTH_MODE=supabase the backend ignores the dev
 * header entirely, and sending both would only confuse a reader of the logs.
 */
export function buildAuthHeaders(opts: {
  devUserId: string | null;
  accessToken: string | null;
}): Record<string, string> {
  if (opts.accessToken) return { Authorization: `Bearer ${opts.accessToken}` };
  if (opts.devUserId) return { "X-Dev-User-Id": opts.devUserId };
  return {};
}

/**
 * Whether a response means the session is over. Only a 401 under real auth:
 * a 503 means the backend could not CHECK the token (Supabase keys
 * unreachable), and signing the user out for that would turn an outage into
 * a forced re-login for everyone.
 */
export function isSessionEnded(status: number, authEnabled: boolean): boolean {
  return authEnabled && status === 401;
}
