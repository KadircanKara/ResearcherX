"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  ArrowLeft,
  Download,
  FileDown,
  Loader2,
  Play,
  Settings2,
  Trash2,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { BinaryPreview } from "@/components/latex/binary-preview";
import { DocumentShareDialog } from "@/components/latex/document-share-dialog";
import { EditorPane } from "@/components/latex/editor-pane";
import { FileTree } from "@/components/latex/file-tree";
import { ImportDropzone } from "@/components/latex/import-dropzone";
import { LogPanel } from "@/components/latex/log-panel";
import { OpenTabs } from "@/components/latex/open-tabs";
import { PdfViewer } from "@/components/latex/pdf-viewer";
import { useLatexDocument } from "@/hooks/use-latex-document";
import { useLatexCompile } from "@/hooks/use-latex-compile";
import {
  AmbiguousMainError,
  downloadExport,
  saveBlob,
  type LatexEngine,
} from "@/lib/latex";
import { buildTree, isBeneath, isTexPath, parentDir } from "@/lib/latex-tree";

const STALE_NOTE = "Out of date — compile to sync";
// SyncTeX speaks paths relative to the main file's own directory, so a file
// outside it has no representable coordinate -- this is a documented
// limitation of the design, not a bug. See `isBeneath` in `lib/latex-tree.ts`.
const OUTSIDE_MAIN_NOTE = "Sync only covers files beside or below the main file.";
// TeX's `l.<n>` is relative to whichever file it was reading -- in a
// multi-file project usually a chapter, often one that is not open. When the
// log does not make that file unambiguous (or it is not in this tree), the
// jump is DECLINED rather than landed on that line of whatever buffer
// happens to be active: a confident wrong jump is worse than no jump.
const LOG_FILE_UNKNOWN_NOTE = "Couldn't tell which file that error is in, so the editor didn't jump.";

interface LatexWorkspaceProps {
  projectId: string;
  documentId: string;
  /** The project's owner, per its member list -- the server refuses a grant
   * naming them (they already resolve to editor ahead of the grant table),
   * so the share dialog renders them with no control. Combined below with
   * the open document's own `created_by` for the same reason. */
  ownerId: string | null;
}

export function LatexWorkspace({ projectId, documentId, ownerId }: LatexWorkspaceProps) {
  const router = useRouter();

  // `useLatexDocument` needs a `canEdit` boolean as an ARGUMENT, before its
  // own return value (`doc`) exists to derive one from -- so this state
  // exists only to satisfy that one circular requirement. It is NOT inert:
  // this is the value that gates the hook's own write paths --
  // `editBuffer`, `createFile`, `removeFile`, `moveFile`, `uploadBinary`,
  // `setMainPath` all check it before doing anything (`use-latex-document.ts`).
  // It lags `canEdit` below by one render, and the effect keys on BOTH
  // `doc.document?.id` and `doc.document?.my_access` -- the same id check
  // as `canEdit` -- so that render-behind lag can only ever be TOWARD
  // false: on a document switch the id stops matching before the new
  // document's access lands, so this closes rather than holding open. Key
  // it on `my_access` alone and the lag reopens exactly the bug `canEdit`
  // below was fixed to close, just one layer down -- a keystroke reaching
  // `editBuffer` for a document the route has already left, saved via a
  // PUT the server then 403s.
  const [hookCanEdit, setHookCanEdit] = useState(false);

  const doc = useLatexDocument(projectId, hookCanEdit, documentId);

  useEffect(() => {
    setHookCanEdit(doc.document?.id === documentId && doc.document?.my_access === "editor");
  }, [doc.document?.id, doc.document?.my_access, documentId]);

  // The component-facing answer, read fresh every render -- no state, no
  // effect, so it can never lag `doc.document` by a render the way the
  // hook-gate above deliberately does. The `doc.document?.id === documentId`
  // half is load-bearing on its own: `documentState` only resets to `null`
  // on the hook's `!docId` branch, so switching between two non-null
  // documents leaves the PREVIOUS document's `my_access` sitting in
  // `doc.document` for one render after `documentId` (the route) has
  // already moved on -- `my_access` alone would fail OPEN for that render
  // (a viewer landing on a document they could edit a moment ago would
  // briefly see compile enabled, delete visible, and a writable editor).
  // Requiring the id match closes that: a document swap can only ever
  // render `canEdit` false until the NEW document's own access lands, never
  // the old one's.
  const canEdit = doc.document?.id === documentId && doc.document?.my_access === "editor";

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
    activePath: doc.activePath,
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

  const [engineOpen, setEngineOpen] = useState(false);

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
    try {
      await downloadExport(projectId, docId, doc.document?.name ?? "export");
    } catch (err) {
      // Routed to the SAME surface every other failure in this shell uses.
      // An empty catch here left a failed export (a 413 over the size cap, a
      // 5xx, a dropped connection) with no surface at all: the button simply
      // appeared inert, which reads as a broken build rather than a failed
      // request. `reportError` applies the hook's own 4xx-shows-the-detail /
      // 5xx-shows-a-generic-line rule, so the size-cap message reaches the
      // user and a server fault's text never does.
      doc.reportError(err);
    }
  }

  function handleDownloadPdf() {
    // From the bytes already in memory, never from the backend's PDF route:
    // that route is keyed on a compile hash in an IN-PROCESS cache, so a
    // link to it 404s for any build this browser did not just make. If
    // there is no `pdfBytes` there is nothing to download, which is why the
    // button is disabled rather than triggering a compile.
    if (!compile.pdfBytes) return;
    const name = doc.document?.name ?? "document";
    saveBlob(new Blob([compile.pdfBytes.slice()], { type: "application/pdf" }), `${name}.pdf`);
  }

  async function handleDeleteDocument() {
    const docId = doc.selectedId;
    if (!docId) return;
    const name = doc.document?.name ?? "this project";
    // The only irreversible control on this screen. Everything else here is
    // a save, a compile or a download.
    if (!window.confirm(`Delete "${name}" and all of its files? This cannot be undone.`)) {
      return;
    }
    await doc.removeDoc(docId);
    router.push(`/research/${projectId}/latex`);
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

  // Same note as `handleChange` above, for `doc.files`/`doc.openFile` and
  // `compile`'s two setters, none of which are stable across a render of the
  // whole `doc` object.
  const handleJumpToError = useCallback(
    (line: number, file: string) => {
      // The compiler already cross-checked this path against the tree it
      // STAGED, so this is not that check repeated -- it is the narrower
      // one only the client can make: the tree may have changed since the
      // compile (a file renamed or deleted while the build was in flight),
      // and a line number means nothing against a buffer that is gone.
      if (!doc.files.some((f) => f.path === file)) {
        compile.setSyncNote(LOG_FILE_UNKNOWN_NOTE);
        return;
      }
      // Opened BEFORE the jump, exactly as `jumpToSource` does it:
      // `gotoLine` only means anything to whichever file is active when the
      // editor reads it.
      void doc.openFile(file).then(() => compile.jumpToLine(line));
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [doc.files, doc.openFile, compile.jumpToLine, compile.setSyncNote]
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
              // Keyed on BOTH halves of the record's identity: two files in
              // the same document can be failing at once, and an id-only key
              // collides between them.
              key={`${f.id}\u0000${f.path}`}
              className="flex items-center justify-between rounded-md border border-destructive/40 bg-destructive/10 px-3 py-1.5 text-xs text-destructive"
            >
              {/* Names the FILE as well as the document: a document-level
                  message could not tell the user which of several open
                  files is the one still unsaved. */}
              <span>
                Changes to {f.path} in {f.name} could not be saved.
              </span>
              <button
                onClick={() => doc.dismissSaveFailure(f.id, f.path)}
                className="text-destructive/70 hover:text-destructive"
              >
                <X className="size-3.5" />
              </button>
            </div>
          ))}
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-2 rounded-xl border border-border px-3 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <Link
            href={`/research/${projectId}/latex`}
            title="All LaTeX projects"
            aria-label="All LaTeX projects"
            className="shrink-0 rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <ArrowLeft className="size-4" />
          </Link>
          {/* Truncated, never sized to its content: a project name is
              user-supplied and unbounded, and this row's width is the one
              thing that used to push the whole page into horizontal scroll. */}
          <span className="truncate text-sm font-medium" title={doc.document?.name}>
            {doc.document?.name ?? "…"}
          </span>
          {doc.error && (
            <span className="truncate text-xs text-destructive" title={doc.error}>
              {doc.error}
            </span>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-1">
          <button
            onClick={handleDownloadPdf}
            disabled={!compile.pdfBytes}
            title={compile.pdfBytes ? "Download PDF" : "Compile first to download a PDF"}
            aria-label="Download PDF"
            className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-40 disabled:hover:bg-transparent"
          >
            <FileDown className="size-4" />
          </button>

          <button
            onClick={() => void handleExport()}
            title="Export .zip"
            aria-label="Export .zip"
            className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <Download className="size-4" />
          </button>

          <DocumentShareDialog
            projectId={projectId}
            documentId={documentId}
            canEdit={canEdit}
            fullAccessUserIds={[ownerId, doc.document?.created_by ?? null].filter(
              (id): id is string => id !== null
            )}
          />

          {canEdit && (
            <button
              onClick={() => void handleDeleteDocument()}
              title="Delete project"
              aria-label="Delete project"
              className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
            >
              <Trash2 className="size-4" />
            </button>
          )}

          {/*
            The engine lives behind this popover rather than on the rail
            because it is ALREADY decided for the user: import picks xelatex
            when the source loads fontspec/unicode-math/polyglossia, which
            hard-fail under pdflatex. Someone who has to change it needs the
            explanation more than they need the control.
          */}
          <div className="relative">
            <button
              onClick={() => setEngineOpen((prev) => !prev)}
              title="Compiler settings"
              aria-label="Compiler settings"
              aria-expanded={engineOpen}
              className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              <Settings2 className="size-4" />
            </button>
            {engineOpen && (
              <div className="absolute right-0 z-20 mt-1 w-72 rounded-lg border border-border bg-popover p-3 text-left shadow-md">
                <label className="text-xs font-medium text-foreground">Engine</label>
                <select
                  value={doc.engine}
                  disabled={!canEdit}
                  onChange={(e) => void doc.setEngine(e.target.value as LatexEngine)}
                  className="mt-1 w-full rounded-md border border-input bg-background px-2 py-1 text-sm"
                >
                  <option value="pdflatex">pdflatex</option>
                  <option value="xelatex">xelatex</option>
                </select>
                <p className="mt-2 text-[11px] leading-snug text-muted-foreground">
                  pdflatex is faster and is what most publisher templates
                  assume. Switch to xelatex if the document loads{" "}
                  <code className="font-mono">fontspec</code>,{" "}
                  <code className="font-mono">unicode-math</code> or{" "}
                  <code className="font-mono">polyglossia</code>, or needs a
                  system font or a non-Latin script.
                </p>
              </div>
            )}
          </div>

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
      </div>

      {/* `selectedId` trails the route by one render (the hook mirrors the
          prop in an effect), so this is a transient state, not the "no
          document" case the list page owns. */}
      {doc.selectedId === null ? (
        <div className="flex-1 animate-pulse rounded-xl bg-muted" />
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
                log={compile.log.text}
                errorFile={compile.log.file}
                errorLine={compile.log.line}
                onClose={() => compile.setLog(null)}
                onJumpToError={handleJumpToError}
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
