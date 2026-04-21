import type { Run } from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export async function createRun(question: string): Promise<Run> {
  const res = await fetch(`${API_BASE}/v1/research`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
  if (!res.ok) throw new Error(`create failed: ${res.status}`);
  return res.json();
}

export async function getRun(id: string): Promise<Run> {
  const res = await fetch(`${API_BASE}/v1/research/${id}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`get failed: ${res.status}`);
  return res.json();
}

export function eventsUrl(id: string): string {
  return `${API_BASE}/v1/research/${id}/events`;
}
