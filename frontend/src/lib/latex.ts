import { API_BASE, getDevUserId } from "./api";

export type LatexEngine = "pdflatex" | "xelatex";

export interface LatexDocument {
  id: string;
  project_id: string;
  name: string;
  // `main_path` names the file that gets compiled (deleting it, or
  // overwriting it with binary, is refused: the backend answers 409).
  // `revision` is the server's own mutation counter; nothing on the client
  // increments it, every mutation response just reports the new value.
  main_path: string;
  revision: number;
  engine: LatexEngine;
  created_at: string;
  updated_at: string;
}

export interface CompileResult {
  ok: boolean;
  log: string;
  /** Present only on success, so a failed compile leaves the last good PDF alone. */
  pdf_hash: string | null;
  /**
   * The document revision this build was made from -- a number the client
   * was HANDED, never one it recomputes. Staleness is
   * `hasUnsavedEdits || document.revision != compiled.revision`, and this is
   * the `compiled.revision` half of that comparison.
   */
  revision: number | null;
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
  /** Tree-relative. Present only alongside `line`. */
  file: string | null;
}

// Every call below goes through this module's own `send`/`sendVoid`, NOT
// `apiSend` from `lib/api.ts`. `apiSend` throws a bare
// `Error("PATCH ... -> 422")`, which carries no server detail at all -- so
// `errorText` in `use-latex-document.ts` fell through to the generic line
// and DISCARDED the backend's own 4xx message on exactly the controls that
// have no other feedback path: "The main file must be a .tex file", "... is
// not a text file in this document", and every 403 behind Set-as-main and
// the engine picker. `apiSend` itself is deliberately untouched -- chat,
// papers and runs all depend on its current behaviour.
//
// The rule these share with the file-tree routes below is unchanged: a 4xx
// is the USER's error and shows the backend's own detail; a 5xx is OURS and
// is replaced with a generic line. See `LatexRequestError`.

export function listDocuments(projectId: string): Promise<LatexDocument[]> {
  return send<LatexDocument[]>(`${API_BASE}/v1/projects/${projectId}/latex`, {
    headers: headers(),
  });
}

export function createDocument(
  projectId: string,
  body: { name: string; source?: string; engine?: LatexEngine }
): Promise<LatexDocument> {
  return send<LatexDocument>(`${API_BASE}/v1/projects/${projectId}/latex`, {
    method: "POST",
    headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
}

export function getDocument(projectId: string, documentId: string): Promise<LatexDocument> {
  return send<LatexDocument>(`${API_BASE}/v1/projects/${projectId}/latex/${documentId}`, {
    headers: headers(),
  });
}

export function patchDocument(
  projectId: string,
  documentId: string,
  body: { name?: string; main_path?: string; engine?: LatexEngine }
): Promise<LatexDocument> {
  return send<LatexDocument>(`${API_BASE}/v1/projects/${projectId}/latex/${documentId}`, {
    method: "PATCH",
    headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
}

export function deleteDocument(projectId: string, documentId: string): Promise<void> {
  // `sendVoid`, not `send`: this route answers 204 with no body at all, and
  // `response.json()` on an empty body throws a SyntaxError that would reach
  // the caller looking exactly like a network failure.
  return sendVoid(`${API_BASE}/v1/projects/${projectId}/latex/${documentId}`, {
    method: "DELETE",
    headers: headers(),
  });
}

export function compileDocument(
  projectId: string,
  documentId: string
): Promise<CompileResult> {
  return send<CompileResult>(
    `${API_BASE}/v1/projects/${projectId}/latex/${documentId}/compile`,
    { method: "POST", headers: headers() }
  );
}

/**
 * Thrown by `fetchPdfBytes` specifically for a 404: the compiler already
 * reported success and handed back a hash, but the cache evicted that build
 * before this fetch ran. Distinguished from every other failure (typed,
 * rather than parsing `.message`) because the correct user-facing story is
 * "compile again to rebuild it", not "the compiler is unavailable" -- the
 * compiler just worked.
 */
export class PdfNotFoundError extends Error {
  constructor() {
    super("pdf not found");
    this.name = "PdfNotFoundError";
  }
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
  if (r.status === 404) throw new PdfNotFoundError();
  if (!r.ok) throw new Error(`GET pdf -> ${r.status}`);
  return new Uint8Array(await r.arrayBuffer());
}

export function synctexForward(
  projectId: string,
  documentId: string,
  line: number,
  file: string
): Promise<ForwardResult> {
  return send<ForwardResult>(
    `${API_BASE}/v1/projects/${projectId}/latex/${documentId}/synctex/forward`,
    {
      method: "POST",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({ line, file }),
    }
  );
}

export function synctexReverse(
  projectId: string,
  documentId: string,
  page: number,
  x: number,
  y: number
): Promise<ReverseResult> {
  return send<ReverseResult>(
    `${API_BASE}/v1/projects/${projectId}/latex/${documentId}/synctex/reverse`,
    {
      method: "POST",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({ page, x, y }),
    }
  );
}

// ---------------------------------------------------------------------------
// File tree
//
// A LaTeX document is a FILE TREE, not a single source string -- one row per
// file on the backend (`latex_files`), with `main_path` naming the file that
// gets compiled. Everything below talks to that tree directly. Tasks 5 and 6
// (the file tree UI and tabs) import from here and nowhere else -- no
// component or hook calls `fetch` directly for any of this.
// ---------------------------------------------------------------------------

export interface LatexFileMeta {
  path: string;
  is_binary: boolean;
  size_bytes: number;
  updated_at: string;
}

export interface LatexTree {
  files: LatexFileMeta[];
  used_bytes: number;
  max_bytes: number;
}

export interface LatexMutation {
  file: LatexFileMeta | null;
  revision: number;
  used_bytes: number;
}

export interface LatexImportResult {
  id: string;
  name: string;
  main_path: string;
  engine: LatexEngine;
  revision: number;
  file_count: number;
}

/**
 * A request the backend refused.
 *
 * `detail` is the server's own message and is shown to the user ONLY for a
 * 4xx -- those are the user's errors (their archive, their quota, their
 * path) and naming the real problem is the whole point. A 5xx is OUR fault
 * and its text is a server implementation detail, so `userMessage` replaces
 * it with a generic line. This is the existing convention in this codebase:
 * sanitize server faults, never sanitize user input errors.
 */
export class LatexRequestError extends Error {
  readonly status: number;
  readonly detail: string | null;
  readonly userMessage: string;

  constructor(status: number, detail: string | null) {
    super(`latex request failed: ${status}`);
    this.name = "LatexRequestError";
    this.status = status;
    this.detail = detail;
    this.userMessage =
      status >= 400 && status < 500 && detail
        ? detail
        : "Something went wrong. Please try again.";
  }
}

/** A 422 the client can act on: re-post the import naming one candidate. */
export class AmbiguousMainError extends Error {
  readonly candidates: string[];
  constructor(candidates: string[]) {
    super("ambiguous main file");
    this.name = "AmbiguousMainError";
    this.candidates = candidates;
  }
}

function headers(extra: Record<string, string> = {}): Record<string, string> {
  const h = { ...extra };
  const devUserId = getDevUserId();
  if (devUserId) h["X-Dev-User-Id"] = devUserId;
  return h;
}

/** Tree-relative, always escaped. Never interpolated into the route. */
function fileUrl(projectId: string, documentId: string, path: string): string {
  return `${API_BASE}/v1/projects/${projectId}/latex/${documentId}/file?path=${encodeURIComponent(path)}`;
}

async function raise(r: Response): Promise<never> {
  let detail: string | null = null;
  try {
    const body = await r.json();
    if (body && typeof body.detail === "object" && body.detail?.error === "ambiguous_main") {
      throw new AmbiguousMainError(body.detail.candidates ?? []);
    }
    detail = typeof body?.detail === "string" ? body.detail : null;
  } catch (err) {
    if (err instanceof AmbiguousMainError) throw err;
    // A non-JSON error body (a proxy's HTML 502 page) is not a message for
    // the user; it falls through to the generic line.
  }
  throw new LatexRequestError(r.status, detail);
}

async function send<T>(url: string, init: RequestInit): Promise<T> {
  const r = await fetch(url, { ...init, cache: "no-store" });
  if (!r.ok) await raise(r);
  return (await r.json()) as T;
}

/** For the one route that answers 204: no body to parse, same error rule. */
async function sendVoid(url: string, init: RequestInit): Promise<void> {
  const r = await fetch(url, { ...init, cache: "no-store" });
  if (!r.ok) await raise(r);
}

export async function listFiles(projectId: string, documentId: string): Promise<LatexTree> {
  return send<LatexTree>(
    `${API_BASE}/v1/projects/${projectId}/latex/${documentId}/files`,
    { headers: headers() }
  );
}

export async function readTextFile(
  projectId: string,
  documentId: string,
  path: string
): Promise<string> {
  const res = await send<{ path: string; content: string }>(
    fileUrl(projectId, documentId, path),
    { headers: headers() }
  );
  return res.content;
}

/**
 * A binary file's raw bytes. The route answers `application/octet-stream`
 * with `nosniff`, so this must never be handed to `<img src>` as a URL --
 * the caller makes an object URL from the Blob instead.
 */
export async function readBinaryFile(
  projectId: string,
  documentId: string,
  path: string
): Promise<Blob> {
  const r = await fetch(fileUrl(projectId, documentId, path), {
    headers: headers(),
    cache: "no-store",
  });
  if (!r.ok) await raise(r);
  return await r.blob();
}

export async function writeTextFile(
  projectId: string,
  documentId: string,
  path: string,
  content: string
): Promise<LatexMutation> {
  return send<LatexMutation>(fileUrl(projectId, documentId, path), {
    method: "PUT",
    headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify({ content }),
  });
}

export async function writeBinaryFile(
  projectId: string,
  documentId: string,
  path: string,
  data: Blob
): Promise<LatexMutation> {
  return send<LatexMutation>(
    `${API_BASE}/v1/projects/${projectId}/latex/${documentId}/file/binary?path=${encodeURIComponent(path)}`,
    {
      method: "POST",
      headers: headers({ "Content-Type": "application/octet-stream" }),
      body: data,
    }
  );
}

export async function deleteFile(
  projectId: string,
  documentId: string,
  path: string
): Promise<LatexMutation> {
  return send<LatexMutation>(fileUrl(projectId, documentId, path), {
    method: "DELETE",
    headers: headers(),
  });
}

export async function renameFile(
  projectId: string,
  documentId: string,
  from: string,
  to: string
): Promise<LatexMutation> {
  return send<LatexMutation>(
    `${API_BASE}/v1/projects/${projectId}/latex/${documentId}/file/rename`,
    {
      method: "POST",
      headers: headers({ "Content-Type": "application/json" }),
      // `from` is a Python keyword, so the backend schema aliases it. These
      // are the WIRE names and must stay exactly these two strings.
      body: JSON.stringify({ from, to }),
    }
  );
}

export async function importArchive(
  projectId: string,
  zip: Blob,
  name: string,
  mainPath?: string
): Promise<LatexImportResult> {
  const query = new URLSearchParams({ name });
  if (mainPath) query.set("main_path", mainPath);
  return send<LatexImportResult>(
    `${API_BASE}/v1/projects/${projectId}/latex/import?${query.toString()}`,
    {
      method: "POST",
      headers: headers({ "Content-Type": "application/zip" }),
      body: zip,
    }
  );
}

/**
 * The export URL, for an ordinary link. Deliberately NOT a fetch: the
 * response carries a `Content-Disposition` filename the browser should
 * honour, and buffering 25MB into JS to re-offer it as an object URL throws
 * that away for nothing.
 *
 * Caveat recorded for whoever wires it: in dev the identity travels in an
 * `X-Dev-User-Id` HEADER, which a plain link cannot send. Task 8 therefore
 * fetches the blob when `getDevUserId()` is set and links directly
 * otherwise.
 */
export function exportUrl(projectId: string, documentId: string): string {
  return `${API_BASE}/v1/projects/${projectId}/latex/${documentId}/export`;
}

export async function fetchExport(projectId: string, documentId: string): Promise<Blob> {
  const r = await fetch(exportUrl(projectId, documentId), {
    headers: headers(),
    cache: "no-store",
  });
  if (!r.ok) await raise(r);
  return await r.blob();
}
