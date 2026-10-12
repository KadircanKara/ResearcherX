import { buildAuthHeaders, isSessionEnded } from "./auth-headers";
import { routes } from "./routes";
import { accessToken, authEnabled, supabase } from "./supabase";
import { ApiError, detailOf } from "./api-error";
import type { Usage } from "./usage";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

let devUserId: string | null = null;
export const setDevUserId = (id: string | null) => { devUserId = id; };
export const getDevUserId = () => devUserId;

/** Identity headers for any backend request: bearer token or dev header. */
export async function authHeaders(): Promise<Record<string, string>> {
  return buildAuthHeaders({ devUserId, accessToken: await accessToken() });
}

/** A 401 under real auth ends the session: sign out and go to /login. */
export async function onAuthFailure(status: number): Promise<void> {
  if (!isSessionEnded(status, authEnabled)) return;
  // Local scope only: a backend 401 (e.g. misconfig) must not revoke the
  // user's sessions on other devices, and needs no network call to succeed.
  await supabase().auth.signOut({ scope: "local" });
  window.location.assign(routes.login());
}

export async function apiGet<T>(path: string): Promise<T> {
  const r = await fetch(`${API_BASE}/v1${path}`, {
    headers: await authHeaders(),
    cache: "no-store",
  });
  await onAuthFailure(r.status);
  if (!r.ok) throw new ApiError(r.status, await detailOf(r), `GET ${path}`);
  return (await r.json()) as T;
}

export async function apiSend<T>(
  method: string,
  path: string,
  body?: unknown
): Promise<T | undefined> {
  const r = await fetch(`${API_BASE}/v1${path}`, {
    method,
    headers: { "Content-Type": "application/json", ...(await authHeaders()) },
    cache: "no-store",
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  await onAuthFailure(r.status);
  if (!r.ok) throw new ApiError(r.status, await detailOf(r), `${method} ${path}`);
  if (r.status === 204) return undefined;
  return (await r.json()) as T;
}

export const fetchUsage = () => apiGet<Usage>("/me/usage");
