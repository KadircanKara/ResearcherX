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
  const res = await fetch(`${API_BASE}/v1/research/${id}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`get failed: ${res.status}`);
  return res.json();
}

export function eventsUrl(id: string): string {
  return `${API_BASE}/v1/research/${id}/events`;
}

let devUserId: string | null = null;
export const setDevUserId = (id: string | null) => { devUserId = id; };
export const getDevUserId = () => devUserId;

export async function apiGet<T>(path: string): Promise<T> {
  const headers: Record<string, string> = {};
  if (devUserId) headers["X-Dev-User-Id"] = devUserId;
  const r = await fetch(`${API_BASE}/v1${path}`, { headers, cache: "no-store" });
  if (!r.ok) throw new Error(`GET ${path} -> ${r.status}`);
  return (await r.json()) as T;
}

export async function apiSend<T>(
  method: string,
  path: string,
  body?: unknown
): Promise<T | undefined> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (devUserId) headers["X-Dev-User-Id"] = devUserId;
  const r = await fetch(`${API_BASE}/v1${path}`, {
    method,
    headers,
    cache: "no-store",
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error(`${method} ${path} -> ${r.status}`);
  if (r.status === 204) return undefined;
  return (await r.json()) as T;
}
