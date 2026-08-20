"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, Play, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { BinaryPreview } from "@/components/latex/binary-preview";
import { EditorPane } from "@/components/latex/editor-pane";
import { FileTree } from "@/components/latex/file-tree";
import { ImportDropzone } from "@/components/latex/import-dropzone";
import { LogPanel } from "@/components/latex/log-panel";
import { OpenTabs } from "@/components/latex/open-tabs";
import { PdfViewer } from "@/components/latex/pdf-viewer";
import { useLatexDocument } from "@/hooks/use-latex-document";
import { useLatexCompile } from "@/hooks/use-latex-compile";
import { getDevUserId } from "@/lib/api";
import {
  AmbiguousMainError,
  exportUrl,
  fetchExport,
  type LatexEngine,
} from "@/lib/latex";
import { buildTree, isBeneath, isTexPath, parentDir } from "@/lib/latex-tree";
import type { Role } from "@/lib/types";

const CAN_EDIT: Role[] = ["owner", "editor"];
const STALE_NOTE = "Out of date — compile to sync";
// SyncTeX speaks paths relative to the main file's own directory, so a file
// outside it has no representable coordinate -- this is a documented
// limitation of the design, not a bug. See `isBeneath` in `lib/latex-tree.ts`.
const OUTSIDE_MAIN_NOTE = "Sync only covers files beside or below the main file.";

// The backend itself defaults a new document's main file to EMPTY -- a
// starter template is a client-side choice, not a server default, so it is
// seeded here and passed explicitly to `createDoc`. Declared at MODULE
// level, not inside the component: a value created during render is a new
// binding every render, which would sit oddly beside every other constant
// here that already isn't.
const STARTER = `\\documentclass[conference]{IEEEtran}
\\begin{document}
\\title{Untitled}
\\author{}
\\maketitle

\\section{Introduction}

\\end{document}
`;

interface LatexWorkspaceProps {
  projectId: string;
  role: Role;
}

export function LatexWorkspace({ projectId, role }: LatexWorkspaceProps) {
  const canEdit = CAN_EDIT.includes(role);

  const doc = useLatexDocument(projectId, canEdit);

  // Scoped to the document `compile()` is about to build, exactly like the
  // in-flight patch it awaits -- an engine change for some OTHER document
  // the user has since switched away from has no bearing on this compile.
  // Read through a ref rather than closed over directly: `beforeCompile` is
  // handed to `useLatexCompile` once per render, but it must always ask
  // about whichever document is CURRENT at the moment the compile actually
  // reaches this point, not whichever was current when the callback was
  // built.
  const selectedIdForCompileRef = useRef(doc.selectedId);
  selectedIdForCompileRef.current = doc.selectedId;
  const beforeCompile = useCallback(async () => {
    const docId = selectedIdForCompileRef.current;
    if (docId) await doc.awaitEnginePatch(docId);
    // `doc.awaitEnginePatch` is the only thing this reads off `doc` -- it is
    // itself a `useCallback` scoped to `[]` in the hook, so listing the
    // whole `doc` object (a fresh literal every render) here would rebuild
    // this callback, and therefore `useLatexCompile`'s `compile`, every
    // single render for no behavioural reason.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doc.awaitEnginePatch]);

  const compile = useLatexCompile({
    projectId,
    documentId: doc.selectedId,
    revision: doc.revision,
    canEdit,
    isDirty: doc.isDirty,
    flushAll: doc.flushAll,
    onOpenFile: doc.openFile,
    beforeCompile,
  });

  // Drag handle. Clamped so neither pane can be dragged out of existence.
  const [splitPercent, setSplitPercent] = useState(60);
  // The active drag's teardown, so an unmount mid-drag can still run it.
  const dragCleanup = useRef<(() => void) | null>(null);

  function startDrag(e: React.PointerEvent<HTMLDivElement>) {
    const handle = e.currentTarget;
    const host = handle.parentElement;
    if (!host) return;
    const box = host.getBoundingClientRect();
    const pointerId = e.pointerId;
    // Capture so move/up/cancel keep reaching this element even once the
    // pointer leaves the 1.5px-wide handle -- true for any drag that moves
    // more than a few pixels, not an edge case.
    handle.setPointerCapture(pointerId);

    const move = (ev: PointerEvent) => {
      const pct = ((ev.clientX - box.left) / box.width) * 100;
      setSplitPercent(Math.min(75, Math.max(25, pct)));
    };
    const stop = () => {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", stop);
      handle.removeEventListener("pointercancel", stop);
      if (handle.hasPointerCapture(pointerId)) {
        handle.releasePointerCapture(pointerId);
      }
      dragCleanup.current = null;
    };

    // pointercancel fires when the browser interrupts tracking (alt-tab, an
    // OS-level gesture, losing focus) -- exactly the case a bare pointerup
    // listener never sees. That gap is what used to leak move/up listeners
    // permanently and leave the pane resizing on unrelated mouse movement
    // afterwards, since nothing was left to remove them.
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", stop);
    handle.addEventListener("pointercancel", stop);
    dragCleanup.current = stop;
  }

  // A drag in progress when the component unmounts (e.g. a route change
  // mid-drag) would otherwise leak its listeners forever -- nothing else is
  // left to ever call `stop` for it.
  useEffect(() => {
    return () => {
      dragCleanup.current?.();
    };
  }, []);

  // Import dialog: open/candidate state only. Everything else about an
  // import (busy, the archive itself) is transient and lives inside the
  // handler below or inside `ImportDropzone` itself.
  const [importOpen, setImportOpen] = useState(false);
  const [importBusy, setImportBusy] = useState(false);
  const [importCandidates, setImportCandidates] = useState<string[]>([]);

  // Mirrors `doc.error`, read from inside `handleImport`'s async callback
  // after `importZip` resolves without throwing -- `doc.error` itself would
  // be the value captured when the callback was BUILT, not the value
  // `importZip` just set moments ago, since `doc` is a fresh object every
  // render.
  const docErrorRef = useRef(doc.error);
  docErrorRef.current = doc.error;

  const [creatingDoc, setCreatingDoc] = useState(false);
  const [newDocName, setNewDocName] = useState("");

  function submitCreateDoc() {
    const trimmed = newDocName.trim();
    setCreatingDoc(false);
    setNewDocName("");
    if (trimmed) void doc.createDoc(trimmed, STARTER);
  }

  function handleImport(zip: File, name: string, mainPath?: string) {
    setImportBusy(true);
    setImportCandidates([]);
    doc
      .importZip(zip, name, mainPath)
      .then(() => {
        // `importZip` only rethrows for an ambiguous main file (caught
        // below); every other failure resolves normally after recording
        // itself in `doc.error`, which is what this checks to decide
        // whether the import actually succeeded.
        if (!docErrorRef.current) {
          setImportOpen(false);
          setImportCandidates([]);
        }
      })
      .catch((err) => {
        if (err instanceof AmbiguousMainError) {
          setImportCandidates(err.candidates);
          return;
        }
        // Anything else here would be a bug in this call, not a
        // user-facing case -- `doc.error` (rendered in the dialog below)
        // covers every real failure.
      })
      .finally(() => setImportBusy(false));
  }

  async function handleExport() {
    const docId = doc.selectedId;
    if (!docId) return;
    // In dev the identity travels in an `X-Dev-User-Id` HEADER, which a
    // plain link cannot send -- so a real fetch is needed there. In prod
    // (cookie-based) a direct navigation lets the browser honour the
    // response's own `Content-Disposition` filename instead of buffering
    // 25MB into JS for nothing.
    if (!getDevUserId()) {
      window.location.href = exportUrl(projectId, docId);
      return;
    }
    try {
      const blob = await fetchExport(projectId, docId);
      const url = URL.createObjectURL(blob);
      const a = window.document.createElement("a");
      a.href = url;
      a.download = `${doc.document?.name ?? "export"}.zip`;
      a.click();
      // Deferred rather than revoked immediately -- see `binary-preview.tsx`
      // for why: Safari can cancel a download still being handed to the OS
      // if its blob: URL is revoked synchronously after `click()`.
      setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch {
      // Nothing to show here beyond `doc.error`-style state -- this is a
      // best-effort download, not a form submission with its own dialog.
    }
  }

  const activePath = doc.activePath;
  const mainDir = doc.mainPath !== null ? parentDir(doc.mainPath) : "";

  // Rebuilt on every render whose `activePath` differs from the last --
  // never read from a ref updated elsewhere. `EditorPane`'s `onChange`
  // carries no path of its own, so this closure is the only thing standing
  // between a keystroke and the file it's supposed to land in; a stale
  // closure here writes one file's typing into another file's buffer.
  // Only `doc.editBuffer` (a `useCallback` scoped to `[canEdit]` in the
  // hook) is read off `doc` here -- see `beforeCompile`'s identical note
  // above for why the whole object is deliberately not listed.
  const handleChange = useCallback(
    (next: string) => {
      if (activePath) doc.editBuffer(activePath, next);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [activePath, doc.editBuffer]
  );

  // Same note as above, for `compile` in place of `doc`.
  const handleLineDoubleClick = useCallback(
    (line: number) => {
      if (!activePath) return;
      if (!isBeneath(activePath, mainDir)) {
        compile.setSyncNote(OUTSIDE_MAIN_NOTE);
        return;
      }
      void compile.jumpToPdf(line, activePath);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [activePath, mainDir, compile.jumpToPdf, compile.setSyncNote]
  );

  if (doc.loading) {
    return <div className="h-[70vh] animate-pulse rounded-xl bg-muted" />;
  }

  const activeMeta = activePath ? doc.files.find((f) => f.path === activePath) : undefined;

  return (
    <div className="flex h-[calc(100vh-14rem)] min-h-[32rem] flex-col gap-2">
      {/*
        A save failure is a fact about the user's TEXT, not about which
        document happens to be on screen -- it must survive a switch away
        from the document that failed, so it is rendered here, unconditionally,
        rather than folded into any per-document badge. No retry control: the
        text lives in a buffer the user may have navigated away from, and
        re-sending it over newer server state is a worse bug than the one
        this surfaces.
      */}
      {doc.saveFailures.length > 0 && (
        <div className="flex flex-col gap-1">
          {doc.saveFailures.map((f) => (
            <div
              key={f.id}
              className="flex items-center justify-between rounded-md border border-destructive/40 bg-destructive/10 px-3 py-1.5 text-xs text-destructive"
            >
              <span>Changes to {f.name} could not be saved.</span>
              <button
                onClick={() => doc.dismissSaveFailure(f.id)}
                className="text-destructive/70 hover:text-destructive"
              >
                <X className="size-3.5" />
              </button>
            </div>
          ))}
        </div>
      )}

      <div className="flex items-center justify-between gap-2 rounded-xl border border-border px-3 py-2">
        <div className="flex items-center gap-2">
          <select
            value={doc.selectedId ?? ""}
            onChange={(e) => doc.select(e.target.value || null)}
            className="rounded-md border border-input bg-background px-2 py-1 text-sm"
          >
            {doc.documents.length === 0 && <option value="">No documents yet</option>}
            {doc.documents.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>

          {creatingDoc ? (
            <input
              autoFocus
              value={newDocName}
              onChange={(e) => setNewDocName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") submitCreateDoc();
                if (e.key === "Escape") {
                  setCreatingDoc(false);
                  setNewDocName("");
                }
              }}
              onBlur={submitCreateDoc}
              placeholder="paper.tex"
              className="rounded-md border border-input bg-background px-2 py-1 text-sm"
            />
          ) : (
            <Button
              size="sm"
              variant="outline"
              disabled={!canEdit}
              title={canEdit ? "New document" : "You need editor access to add a document"}
              onClick={() => setCreatingDoc(true)}
            >
              New
            </Button>
          )}

          <Button
            size="sm"
            variant="outline"
            disabled={!canEdit || !doc.selectedId}
            title={canEdit ? "Delete document" : "You need editor access to delete a document"}
            onClick={() => doc.selectedId && void doc.removeDoc(doc.selectedId)}
          >
            Delete
          </Button>

          {doc.error && <span className="text-xs text-destructive">{doc.error}</span>}
        </div>

        {doc.selectedId && (
          <div className="flex items-center gap-2">
            <select
              value={doc.engine}
              disabled={!canEdit}
              onChange={(e) => void doc.setEngine(e.target.value as LatexEngine)}
              className="rounded-md border border-input bg-background px-1.5 py-0.5 text-[11px]"
            >
              <option value="pdflatex">pdflatex</option>
              <option value="xelatex">xelatex</option>
            </select>
            <Button
              size="sm"
              className="h-7 gap-1 px-2 text-[11px]"
              disabled={!canEdit || compile.compiling}
              title={canEdit ? "Compile (Cmd/Ctrl+S)" : "You need editor access to compile"}
              onClick={() => void compile.compile()}
            >
              {compile.compiling ? (
                <Loader2 className="size-3 animate-spin" />
              ) : (
                <Play className="size-3" />
              )}
              Compile
            </Button>
          </div>
        )}
      </div>

      {doc.selectedId === null ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 text-sm text-muted-foreground">
          <p>Select a document, or create one to start writing.</p>
        </div>
      ) : (
        <div className="relative flex flex-1 overflow-hidden rounded-xl border border-border">
          <FileTree
            nodes={buildTree(doc.files)}
            activePath={doc.activePath}
            mainPath={doc.mainPath}
            canEdit={canEdit}
            usedBytes={doc.usedBytes}
            maxBytes={doc.maxBytes}
            error={doc.error}
            onOpen={(path) => void doc.openFile(path)}
            onCreate={(path) => void doc.createFile(path)}
            onDelete={(path) => void doc.removeFile(path)}
            onRename={(from, to) => void doc.moveFile(from, to)}
            onSetMain={(path) => void doc.setMainPath(path)}
            onUpload={(path, data) => void doc.uploadBinary(path, data)}
            onImportClick={() => {
              setImportCandidates([]);
              setImportOpen(true);
            }}
            onExport={() => void handleExport()}
          />

          <div style={{ width: `${splitPercent}%` }} className="flex flex-col overflow-hidden">
            <OpenTabs
              paths={doc.openPaths}
              activePath={doc.activePath}
              dirtyPaths={doc.dirtyPaths}
              onSelect={(path) => void doc.openFile(path)}
              onClose={doc.closeFile}
            />
            <div className="flex items-center justify-end border-b border-border px-3 py-1 text-[11px] text-muted-foreground">
              {doc.saveState === "saving" && "Saving…"}
              {doc.saveState === "error" && (
                <span className="text-destructive">Could not save</span>
              )}
            </div>
            <div className="flex-1 overflow-hidden">
              {activePath === null ? (
                <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                  Select or create a file to start writing.
                </div>
              ) : /*
                  BOTH signals, never `isTexPath` alone. The two answer
                  DIFFERENT questions (see `isTexPath`'s own comment in
                  `lib/latex-tree.ts`): `is_binary` is how the backend STORED
                  the bytes, `isTexPath` is whether a human should be shown a
                  text buffer. A `.bib`/`.sty`/`.bst` in latin-1 out of a real
                  Overleaf or arXiv project decodes as binary and is stored
                  that way, and so is every file uploaded through the file
                  tree whatever its extension. `openFile` correctly skips the
                  fetch and the buffer for such a path -- so routing on the
                  extension alone rendered an EMPTY editor over it, and the
                  first keystroke PUT that empty buffer through `write_text`,
                  which sets `is_binary=False` and `blob=None`. The original
                  bytes were gone permanently and silently. Do not
                  re-simplify this to one test.
                */
              isTexPath(activePath) && !activeMeta?.is_binary ? (
                <EditorPane
                  path={activePath}
                  openPaths={doc.openPaths}
                  value={doc.buffers[activePath] ?? ""}
                  onChange={handleChange}
                  onLineDoubleClick={handleLineDoubleClick}
                  gotoLine={compile.gotoLine}
                  readOnly={!canEdit}
                />
              ) : (
                <BinaryPreview
                  projectId={projectId}
                  documentId={doc.selectedId}
                  path={activePath}
                  sizeBytes={activeMeta?.size_bytes ?? 0}
                />
              )}
            </div>
          </div>

          <div
            onPointerDown={startDrag}
            className="w-1.5 shrink-0 cursor-col-resize bg-border transition-colors hover:bg-primary/40"
            role="separator"
            aria-orientation="vertical"
          />

          <div className="flex flex-1 flex-col overflow-hidden">
            <div className="flex items-center justify-between border-b border-border px-3 py-1.5 text-xs">
              <div className="flex items-center gap-2">
                <span className="text-muted-foreground">Preview</span>
                {compile.pdfBytes && compile.stale && (
                  <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-[11px] font-medium text-amber-700 dark:text-amber-400">
                    {STALE_NOTE}
                  </span>
                )}
                {/* `compile.syncNote` is already gated inside the hook: the
                    "PDF is out of date" message can never render while the
                    PDF is not, in fact, out of date. Every OTHER message
                    (declined-sync, "no place matches", "unavailable") passes
                    through unconditionally. */}
                {compile.syncNote && (
                  <span className="text-[11px] text-muted-foreground">{compile.syncNote}</span>
                )}
              </div>
            </div>
            <div className="flex-1 overflow-hidden">
              <PdfViewer
                bytes={compile.pdfBytes}
                scale={1.25}
                highlight={compile.highlight}
                scrollToPage={compile.scrollToPage}
                onPageDoubleClick={(page, point) => void compile.jumpToSource(page, point)}
              />
            </div>
            {compile.log !== null && (
              <LogPanel
                log={compile.log}
                onClose={() => compile.setLog(null)}
                onJumpToLine={compile.jumpToLine}
              />
            )}
          </div>
        </div>
      )}

      <ImportDropzone
        open={importOpen}
        busy={importBusy}
        error={doc.error}
        candidates={importCandidates}
        onClose={() => {
          setImportOpen(false);
          setImportCandidates([]);
        }}
        onImport={handleImport}
      />
    </div>
  );
}
