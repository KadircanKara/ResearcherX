import { API_BASE, apiGet, apiSend, getDevUserId } from "./api";

export type LatexEngine = "pdflatex" | "xelatex";

export interface LatexDocument {
  id: string;
  project_id: string;
  name: string;
  source: string;
  engine: LatexEngine;
  created_at: string;
  updated_at: string;
}

export interface CompileResult {
  ok: boolean;
  log: string;
  /** Present only on success, so a failed compile leaves the last good PDF alone. */
  pdf_hash: string | null;
}

export interface ForwardResult {
  found: boolean;
  page: number | null;
  x: number | null;
  y: number | null;
  width: number | null;
  height: number | null;
}

export interface ReverseResult {
  found: boolean;
  line: number | null;
}

export function listDocuments(projectId: string): Promise<LatexDocument[]> {
  return apiGet<LatexDocument[]>(`/projects/${projectId}/latex`);
}

export async function createDocument(
  projectId: string,
  body: { name: string; source?: string; engine?: LatexEngine }
): Promise<LatexDocument> {
  const doc = await apiSend<LatexDocument>("POST", `/projects/${projectId}/latex`, body);
  if (!doc) throw new Error("create failed: no body");
  return doc;
}

export function getDocument(projectId: string, documentId: string): Promise<LatexDocument> {
  return apiGet<LatexDocument>(`/projects/${projectId}/latex/${documentId}`);
}

export async function patchDocument(
  projectId: string,
  documentId: string,
  body: { name?: string; source?: string; engine?: LatexEngine }
): Promise<LatexDocument> {
  const doc = await apiSend<LatexDocument>(
    "PATCH",
    `/projects/${projectId}/latex/${documentId}`,
    body
  );
  if (!doc) throw new Error("patch failed: no body");
  return doc;
}

export async function deleteDocument(projectId: string, documentId: string): Promise<void> {
  await apiSend<void>("DELETE", `/projects/${projectId}/latex/${documentId}`);
}

export async function compileDocument(
  projectId: string,
  documentId: string
): Promise<CompileResult> {
  const res = await apiSend<CompileResult>(
    "POST",
    `/projects/${projectId}/latex/${documentId}/compile`
  );
  if (!res) throw new Error("compile failed: no body");
  return res;
}

/**
 * The PDF bytes for one build.
 *
 * Raw fetch rather than apiGet because the response is application/pdf, not
 * JSON -- but it still carries the identity header, or the request is a
 * different user's.
 *
 * Returns a fresh Uint8Array. pdf.js DETACHES the buffer it is handed, so the
 * caller must pass a copy to each getDocument call, not this array itself.
 */
export async function fetchPdfBytes(
  projectId: string,
  documentId: string,
  hash: string
): Promise<Uint8Array> {
  const headers: Record<string, string> = {};
  const devUserId = getDevUserId();
  if (devUserId) headers["X-Dev-User-Id"] = devUserId;
  const r = await fetch(
    `${API_BASE}/v1/projects/${projectId}/latex/${documentId}/pdf?hash=${encodeURIComponent(hash)}`,
    { headers, cache: "no-store" }
  );
  if (!r.ok) throw new Error(`GET pdf -> ${r.status}`);
  return new Uint8Array(await r.arrayBuffer());
}

export async function synctexForward(
  projectId: string,
  documentId: string,
  line: number
): Promise<ForwardResult> {
  const res = await apiSend<ForwardResult>(
    "POST",
    `/projects/${projectId}/latex/${documentId}/synctex/forward`,
    { line }
  );
  if (!res) throw new Error("forward failed: no body");
  return res;
}

export async function synctexReverse(
  projectId: string,
  documentId: string,
  page: number,
  x: number,
  y: number
): Promise<ReverseResult> {
  const res = await apiSend<ReverseResult>(
    "POST",
    `/projects/${projectId}/latex/${documentId}/synctex/reverse`,
    { page, x, y }
  );
  if (!res) throw new Error("reverse failed: no body");
  return res;
}
