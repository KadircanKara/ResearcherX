/**
 * Hand a Blob to the browser as a download.
 *
 * Its own module because three unrelated features need it -- a compiled
 * PDF, a project export, a chat transcript -- and importing a download
 * helper out of the LaTeX client to save a conversation would tie two
 * features together that share nothing but this line.
 */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = window.document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  // The revoke is DEFERRED, never synchronous after `click()`: Safari can
  // cancel a download still being handed to the OS if its `blob:` URL is
  // revoked in the same tick. See `binary-preview.tsx`, which hit exactly
  // this.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
