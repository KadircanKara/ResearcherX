/**
 * Which files the Upload tab takes, and how a stored file is named on the
 * way back out.
 *
 * The backend (`app/services/text_extraction.py`) is the enforcement: it
 * checks the extension AND the bytes, and answers a 422 the tab shows
 * verbatim. This list exists so an unsupported file is skipped visibly
 * before any upload, the same way `MAX_MENTIONS` is a UX copy of a server
 * limit. Keep the two in step.
 */

/** Exactly these; nothing else. */
export const SUPPORTED_EXTENSIONS = [".pdf", ".docx", ".md", ".markdown", ".txt", ".rtf"] as const;

/** The file input's `accept` attribute. */
export const UPLOAD_ACCEPT = SUPPORTED_EXTENSIONS.join(",");

/** How the drop zone names the formats. */
export const SUPPORTED_LABEL = "PDF, DOCX, Markdown, TXT or RTF";

/**
 * The lower-case extension of a supported file name, without the dot
 * ("docx"), or null when the name has no supported extension.
 */
export function uploadExtension(name: string): string | null {
  const dot = name.lastIndexOf(".");
  if (dot < 0) return null;
  const ext = name.slice(dot).toLowerCase();
  return (SUPPORTED_EXTENSIONS as readonly string[]).includes(ext) ? ext.slice(1) : null;
}

/** Only a PDF goes through the suggested-title step; it reads PDF bytes. */
export function isPdfName(name: string): boolean {
  return uploadExtension(name) === "pdf";
}

const EXTENSION_BY_TYPE: Record<string, string> = {
  "application/pdf": ".pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
  "text/markdown": ".md",
  "text/plain": ".txt",
  "application/rtf": ".rtf",
};

/**
 * The extension a downloaded paper file is saved under, from the type the
 * backend served it with. Anything unknown is a PDF: every file stored
 * before the other formats existed is one.
 */
export function extensionForType(contentType: string): string {
  const base = contentType.split(";", 1)[0].trim().toLowerCase();
  return EXTENSION_BY_TYPE[base] ?? ".pdf";
}
