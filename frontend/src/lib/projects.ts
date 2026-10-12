import { apiGet, apiSend, authHeaders, onAuthFailure, API_BASE } from "./api";
import { ApiError, detailOf } from "./api-error";
import type { Project, ProjectDetail, Paper, PaperSource } from "./types";

export async function listProjects(): Promise<Project[]> {
  return apiGet<Project[]>("/projects");
}

export async function createProject(body: {
  title: string;
  description?: string | null;
  topic_keywords?: string[];
  /** Must be a `PROJECT_COLORS` entry; the server 422s anything else. */
  color?: string;
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
    /** Must be a `PROJECT_COLORS` entry; the server 422s anything else. */
    color?: string;
  }
): Promise<Project> {
  return (await apiSend<Project>("PATCH", `/projects/${id}`, body)) as Project;
}

export async function deleteProject(id: string): Promise<void> {
  await apiSend<void>("DELETE", `/projects/${id}`);
}

export async function listPapers(projectId: string): Promise<Paper[]> {
  return apiGet<Paper[]>(`/projects/${projectId}/papers`);
}

/**
 * The stored PDF for an uploaded paper. Throws when there is none (404).
 *
 * Fetched rather than exposed as a plain link: the identity travels in a
 * HEADER (bearer token, or `X-Dev-User-Id` in dev), which an `<a href>` cannot send -- the same
 * caveat `downloadExport` carries in the LaTeX client. Keeping the header
 * handling here means the page never assembles a URL of its own.
 */
export async function fetchPaperPdf(projectId: string, paperId: string): Promise<Blob> {
  const headers: Record<string, string> = {};
  Object.assign(headers, await authHeaders());
  const r = await fetch(`${API_BASE}/v1/projects/${projectId}/papers/${paperId}/pdf`, {
    headers,
    cache: "no-store",
  });
  await onAuthFailure(r.status);
  if (!r.ok) throw new Error(`paper pdf -> ${r.status}`);
  return r.blob();
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

/**
 * Upload a paper's file as the raw body. `ext` ("pdf", "docx", "md", …) is
 * how the backend knows the format, which it then confirms against the
 * bytes; only the extension is sent, never the file name.
 */
export async function ingestPaper(
  projectId: string,
  paperId: string,
  fileBytes: ArrayBuffer,
  ext: string
): Promise<{ chunks_stored: number }> {
  const headers: Record<string, string> = {
    "Content-Type": "application/octet-stream",
  };
  Object.assign(headers, await authHeaders());
  const r = await fetch(
    `${API_BASE}/v1/projects/${projectId}/papers/${paperId}/ingest?ext=${encodeURIComponent(ext)}`,
    { method: "POST", headers, body: fileBytes, cache: "no-store" }
  );
  await onAuthFailure(r.status);
  if (!r.ok) throw new ApiError(r.status, await detailOf(r), "ingest");
  return r.json();
}

export async function suggestTitle(
  projectId: string,
  pdfBytes: ArrayBuffer
): Promise<{ title: string | null; abstract: string | null; body: string | null }> {
  const headers: Record<string, string> = {
    "Content-Type": "application/octet-stream",
  };
  Object.assign(headers, await authHeaders());
  const r = await fetch(
    `${API_BASE}/v1/projects/${projectId}/papers/suggest-title`,
    { method: "POST", headers, body: pdfBytes, cache: "no-store" }
  );
  await onAuthFailure(r.status);
  if (!r.ok) return { title: null, abstract: null, body: null };
  return r.json();
}

export interface PaperChunk {
  chunk_index: number;
  text: string;
  paper_title: string;
  /**
   * Where this chunk sits in the paper, as the chunk is indexed TODAY.
   *
   * The hover card deliberately shows the citation's own snapshot of these
   * instead (see components/chat/citation-chip.tsx): a citation records where the
   * excerpt was when the answer was written, and a re-index can move it.
   * Declared here because the API returns them and a type that omits half a
   * response is a type that lies.
   *
   * Empty/null for every chunk indexed before structured chunking.
   */
  section: string[];
  page: number | null;
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
