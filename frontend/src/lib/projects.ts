import { apiGet, apiSend, getDevUserId, API_BASE } from "./api";
import type { Project, ProjectDetail, Member, Role, Run, Paper, PaperSource } from "./types";

export async function listProjects(): Promise<Project[]> {
  return apiGet<Project[]>("/projects");
}

export async function createProject(body: {
  title: string;
  description?: string | null;
  topic_keywords?: string[];
}): Promise<Project> {
  return (await apiSend<Project>("POST", "/projects", body)) as Project;
}

export async function getProject(id: string): Promise<ProjectDetail> {
  return apiGet<ProjectDetail>(`/projects/${id}`);
}

export async function updateProject(
  id: string,
  body: {
    title?: string;
    description?: string | null;
    topic_keywords?: string[];
  }
): Promise<Project> {
  return (await apiSend<Project>("PATCH", `/projects/${id}`, body)) as Project;
}

export async function deleteProject(id: string): Promise<void> {
  await apiSend<void>("DELETE", `/projects/${id}`);
}

export async function listMembers(id: string): Promise<Member[]> {
  return apiGet<Member[]>(`/projects/${id}/members`);
}

export async function addMember(
  id: string,
  body: { user_id: string; role: Role }
): Promise<Member> {
  return (await apiSend<Member>("POST", `/projects/${id}/members`, body)) as Member;
}

export async function updateMemberRole(
  id: string,
  userId: string,
  body: { role: Role }
): Promise<Member> {
  return (await apiSend<Member>("PATCH", `/projects/${id}/members/${userId}`, body)) as Member;
}

export async function removeMember(id: string, userId: string): Promise<void> {
  await apiSend<void>("DELETE", `/projects/${id}/members/${userId}`);
}

export async function listProjectRuns(
  projectId: string,
  limit = 20,
  offset = 0,
): Promise<Run[]> {
  return apiGet<Run[]>(`/projects/${projectId}/runs?limit=${limit}&offset=${offset}`);
}

export async function listPapers(projectId: string): Promise<Paper[]> {
  return apiGet<Paper[]>(`/projects/${projectId}/papers`);
}

export async function createPaper(
  projectId: string,
  data: {
    title: string;
    abstract?: string | null;
    body?: string | null;
    pdf_url?: string | null;
    source: PaperSource;
  }
): Promise<Paper> {
  return (await apiSend<Paper>("POST", `/projects/${projectId}/papers`, data)) as Paper;
}

export async function patchPaper(
  projectId: string,
  paperId: string,
  data: { title?: string; abstract?: string | null; body?: string | null }
): Promise<Paper> {
  return (await apiSend<Paper>(
    "PATCH",
    `/projects/${projectId}/papers/${paperId}`,
    data
  )) as Paper;
}

export async function deletePaper(projectId: string, paperId: string): Promise<void> {
  await apiSend("DELETE", `/projects/${projectId}/papers/${paperId}`);
}

export async function ingestPaper(
  projectId: string,
  paperId: string,
  pdfBytes: ArrayBuffer
): Promise<{ chunks_stored: number }> {
  const headers: Record<string, string> = {
    "Content-Type": "application/octet-stream",
  };
  const uid = getDevUserId();
  if (uid) headers["X-Dev-User-Id"] = uid;
  const r = await fetch(
    `${API_BASE}/v1/projects/${projectId}/papers/${paperId}/ingest`,
    { method: "POST", headers, body: pdfBytes, cache: "no-store" }
  );
  if (!r.ok) throw new Error(`ingest -> ${r.status}`);
  return r.json();
}

export async function suggestTitle(
  projectId: string,
  pdfBytes: ArrayBuffer
): Promise<{ title: string | null; abstract: string | null; body: string | null }> {
  const headers: Record<string, string> = {
    "Content-Type": "application/octet-stream",
  };
  const uid = getDevUserId();
  if (uid) headers["X-Dev-User-Id"] = uid;
  const r = await fetch(
    `${API_BASE}/v1/projects/${projectId}/papers/suggest-title`,
    { method: "POST", headers, body: pdfBytes, cache: "no-store" }
  );
  if (!r.ok) return { title: null, abstract: null, body: null };
  return r.json();
}

export async function suggestTitleFromUrl(
  projectId: string,
  url: string
): Promise<{ title: string | null; abstract: string | null; requires_manual: boolean }> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const uid = getDevUserId();
  if (uid) headers["X-Dev-User-Id"] = uid;
  const r = await fetch(
    `${API_BASE}/v1/projects/${projectId}/papers/suggest-title-from-url`,
    { method: "POST", headers, body: JSON.stringify({ url }), cache: "no-store" }
  );
  if (!r.ok) return { title: null, abstract: null, requires_manual: true };
  return r.json();
}

export async function ingestPaperFromUrl(
  projectId: string,
  paperId: string,
  url: string
): Promise<{ chunks_stored: number }> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const uid = getDevUserId();
  if (uid) headers["X-Dev-User-Id"] = uid;
  const r = await fetch(
    `${API_BASE}/v1/projects/${projectId}/papers/${paperId}/ingest-from-url`,
    {
      method: "POST",
      headers,
      body: JSON.stringify({ url }),
      cache: "no-store",
    }
  );
  if (r.status === 422) {
    const body = await r.json().catch(() => ({}));
    const err = new Error("ingest-from-url -> 422") as Error & { paywalled?: boolean };
    err.paywalled = body?.detail?.error === "paywalled";
    throw err;
  }
  if (r.status === 503) {
    // The PDF was fetched and parsed — only indexing is down. Distinct from a
    // fetch failure so the UI doesn't tell the user their link is bad.
    const err = new Error("ingest-from-url -> 503") as Error & { unavailable?: boolean };
    err.unavailable = true;
    throw err;
  }
  if (!r.ok) throw new Error(`ingest-from-url -> ${r.status}`);
  return r.json();
}

export interface PaperChunk {
  chunk_index: number;
  text: string;
  paper_title: string;
}

export async function getPaperChunk(
  projectId: string,
  paperId: string,
  chunkIndex: number
): Promise<PaperChunk> {
  return apiGet<PaperChunk>(
    `/projects/${projectId}/papers/${paperId}/chunks/${chunkIndex}`
  );
}
