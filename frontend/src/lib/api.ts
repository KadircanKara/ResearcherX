import { buildAuthHeaders, isSessionEnded } from "./auth-headers";
import { routes } from "./routes";
import { accessToken, authEnabled, supabase } from "./supabase";
import type { Run } from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export async function createRun(question: string, projectId?: string): Promise<Run> {
  const run = await apiSend<Run>("POST", "/research", {
    question,
    project_id: projectId ?? null,
  });
  if (!run) throw new Error("create failed: no body");
  return run;
}

export async function getRun(id: string): Promise<Run> {
  const res = await fetch(`${API_BASE}/v1/research/${id}`, {
    headers: await authHeaders(),
    cache: "no-store",
  });
  await onAuthFailure(res.status);
  if (!res.ok) throw new Error(`get failed: ${res.status}`);
  return res.json();
}

export function eventsUrl(id: string): string {
  return `${API_BASE}/v1/research/${id}/events`;
}

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
  await supabase().auth.signOut();
  window.location.assign(routes.login());
}

export async function apiGet<T>(path: string): Promise<T> {
  const r = await fetch(`${API_BASE}/v1${path}`, {
    headers: await authHeaders(),
    cache: "no-store",
  });
  await onAuthFailure(r.status);
  if (!r.ok) throw new Error(`GET ${path} -> ${r.status}`);
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
  if (!r.ok) throw new Error(`${method} ${path} -> ${r.status}`);
  if (r.status === 204) return undefined;
  return (await r.json()) as T;
}
