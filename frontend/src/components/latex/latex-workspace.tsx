"use client";

import { routes } from "@/lib/routes";
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { useRouter } from "next/navigation";
import { FilePlus2, Upload, UploadCloud, X } from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable";
import { BinaryPreview } from "@/components/latex/binary-preview";
import { ConflictDialog } from "@/components/latex/conflict-dialog";
import { DocumentShareDialog } from "@/components/latex/document-share-dialog";
import {
  EditorDeleteDialog,
  EditorNewFileDialog,
  EditorRenameDialog,
} from "@/components/latex/editor-dialogs";
import { EditorPane } from "@/components/latex/editor-pane";
import { EditorToolbar, type ViewMode } from "@/components/latex/editor-toolbar";
import { FileTree } from "@/components/latex/file-tree";
import { ImportDropzone } from "@/components/latex/import-dropzone";
import { LogPanel } from "@/components/latex/log-panel";
import { OpenTabs } from "@/components/latex/open-tabs";
import { PdfViewer } from "@/components/latex/pdf-viewer";
import { useLatexDocument } from "@/hooks/use-latex-document";
import { useLatexCompile } from "@/hooks/use-latex-compile";
import {
  NameCollisionError,
  PathCollisionError,
  downloadExport,
  errorText,
  saveBlob,
  type LatexCollision,
} from "@/lib/latex";
import { buildTree, formatBytes, isBeneath, isTexPath, parentDir } from "@/lib/latex-tree";
import { cn } from "@/lib/utils";

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

// The prototype lays the editor out twice -- a stacked column under `md` and
// resizable panels from `md` up -- and lets CSS hide one. Here only ONE is
// ever mounted: CodeMirror and pdf.js each hold real state (undo history, a
// parsed PDF, a live worker), and a hidden second copy would double every
// render and split every jump between two editors. Same breakpoint as `md:`.
const DESKTOP_QUERY = "(min-width: 768px)";

function subscribeDesktop(onChange: () => void): () => void {
  const mq = window.matchMedia(DESKTOP_QUERY);
  mq.addEventListener("change", onChange);
  return () => mq.removeEventListener("change", onChange);
}

function useIsDesktop(): boolean {
  return useSyncExternalStore(
    subscribeDesktop,
    () => window.matchMedia(DESKTOP_QUERY).matches,
    () => true
  );
}

interface LatexWorkspaceProps {
  projectId: string;
  documentId: string;
  /** The project's owner, per its member list -- the server refuses a grant
   * naming them (they already resolve to editor ahead of the grant table),
   * so the share dialog renders them with no control. Combined below with
   * the open document's own `created_by` for the same reason. */
  ownerId: string | null;
}

/**
 * The LaTeX editor, laid out as the prototype's `LatexEditorWorkspace`:
 * one bordered frame holding the toolbar, the file tree, the open-file tabs,
 * source and PDF side by side, and the compile bar. The prototype's editor
 * is a mock; everything behind this layout is the real engine.
 */
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

  const isDesktop = useIsDesktop();

  // The editor opens on the main file, as the prototype's does -- ONCE per
  // document: a user who then closes every tab has asked for an empty
  // editor, and re-opening the main file under them would undo that.
  // Waits for the document's own tree, so it never opens a path the server
  // has not listed, and for the route's document to be the selected one, so
  // it can never open a file in the document being left.
  const autoOpenedFor = useRef<string | null>(null);
  const openMainFile = doc.openFile;
  useEffect(() => {
    if (autoOpenedFor.current === documentId) return;
    if (doc.selectedId !== documentId || doc.document?.id !== documentId) return;
    const main = doc.mainPath;
    if (!main || !doc.files.some((f) => f.path === main)) return;
    autoOpenedFor.current = documentId;
    if (doc.openPaths.length === 0) void openMainFile(main);
  }, [documentId, doc.selectedId, doc.document?.id, doc.mainPath, doc.files, doc.openPaths.length, openMainFile]);

  // Which panes are on screen. The editor and the preview are the only two
  // that answer to this; the file tree has its own panel and width.
  const [viewMode, setViewMode] = useState<ViewMode>("split");

  const [shareOpen, setShareOpen] = useState(false);

  // Import dialog: open state only. The two-step plan/commit conversation
  // (and its own busy/error) lives inside `ImportDropzone`, which the
  // projects list page shares.
  const [importOpen, setImportOpen] = useState(false);
  // The archive the "Upload file" control handed over, so the import dialog
  // opens with it already chosen. Cleared with the dialog.
  const [importFile, setImportFile] = useState<File | null>(null);

  /**
   * One file picked from the "Upload file" control, routed by what it IS.
   *
   * A `.zip` goes through the merge-import flow so it lands as real files:
   * LaTeX resolves `\input` and `\includegraphics` against paths on disk
   * and cannot read inside an archive, and the compiler runs with
   * `-no-shell-escape` so a `\write18` unzip is blocked too. Stored as-is
   * a `.zip` would be unreferenceable dead weight counting against the
   * project's byte cap.
   *
   * Everything else is written straight into the tree root, through the
   * same collision path as every other write.
   */
  function handleAddFile(file: File) {
    if (/\.zip$/i.test(file.name)) {
      setImportFile(file);
      setImportOpen(true);
      return;
    }
    void withConflicts((p) => doc.uploadBinary(p, file), file.name);
  }

  // The duplicate-name question for the TREE surfaces (new file, upload,
  // rename, duplicate). `retry` is the same operation the user already asked
  // for, re-issued at whatever path they settle on -- never a second,
  // different call built from the collision.
  const [conflict, setConflict] = useState<{
    collisions: LatexCollision[];
    retry: (path: string) => Promise<void>;
  } | null>(null);
  const [conflictBusy, setConflictBusy] = useState(false);

  /**
   * Runs a tree mutation and turns its 409 into the conflict dialog.
   *
   * The hook rethrows `PathCollisionError` UNCHANGED for exactly this: the
   * dialog is built from the server's own `suggestion`, which a sentence in
   * `doc.error` could not carry. Every other failure keeps going to the
   * hook's single error surface.
   */
  async function withConflicts(
    run: (path: string) => Promise<void>,
    path: string
  ): Promise<void> {
    try {
      await run(path);
    } catch (err) {
      if (err instanceof PathCollisionError) {
        setConflict({ collisions: err.collisions, retry: run });
        return;
      }
      doc.reportError(err);
    }
  }

  async function confirmConflict(decisions: { path: string; new_path: string }[]) {
    const pending = conflict;
    const target = decisions[0]?.new_path;
    if (!pending || !target) return;
    setConflictBusy(true);
    try {
      // The retry can collide AGAIN (a second user created that name while
      // this dialog was open), so it goes back through `withConflicts`
      // rather than being called bare.
      await withConflicts(pending.retry, target);
    } finally {
      setConflictBusy(false);
    }
    // Closed unconditionally: if the retry collided again, `withConflicts`
    // has already replaced `conflict` with the new collision, and clearing
    // it here would throw that away. Only the entry this call opened is
    // dismissed.
    setConflict((current) => (current === pending ? null : current));
  }

  // New file. The path goes to the server exactly as typed (trimmed) and a
  // refusal comes back into the dialog in the server's own words.
  const [newFile, setNewFile] = useState<{ open: boolean; initial: string }>({
    open: false,
    initial: "",
  });
  const [newFileBusy, setNewFileBusy] = useState(false);
  const [newFileError, setNewFileError] = useState<string | null>(null);

  function openNewFile(dir: string) {
    setNewFileError(null);
    setNewFile({ open: true, initial: dir ? `${dir}/` : "" });
  }

  // Creates, then opens -- but only a file that landed in the document still
  // on screen (`createFile` resolves false otherwise), so a slow create can
  // never open a path in a document the user has since left.
  const createAndOpen = useCallback(
    async (path: string) => {
      if (await doc.createFile(path)) await doc.openFile(path);
    },
    // Only these two are read off `doc`; see `beforeCompile` above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [doc.createFile, doc.openFile]
  );

  async function handleCreateFile(path: string) {
    setNewFileBusy(true);
    setNewFileError(null);
    try {
      await createAndOpen(path);
      setNewFile((s) => ({ ...s, open: false }));
    } catch (err) {
      if (err instanceof PathCollisionError) {
        // Handed to the shared conflict dialog, which offers the server's
        // `(n)` suggestion and re-runs the SAME create at the chosen path.
        setNewFile((s) => ({ ...s, open: false }));
        setConflict({ collisions: err.collisions, retry: createAndOpen });
        return;
      }
      setNewFileError(errorText(err));
    } finally {
      setNewFileBusy(false);
    }
  }

  /**
   * Duplicate the active text file. The copy is written at the ORIGINAL
   * path with the default `if_exists=fail`, so the server answers 409 with
   * its own `(n)` suggestion -- the conflict dialog then re-sends the same
   * write at whatever path the user settles on. The browser never invents
   * the suffix. The text is the buffer on screen, unsaved edits included.
   */
  function handleDuplicate() {
    const path = doc.activePath;
    if (!path) return;
    const text = doc.buffers[path];
    if (text === undefined) return;
    void withConflicts(async (p) => {
      await doc.createFile(p, text);
    }, path);
  }

  async function handleExport() {
    const docId = doc.selectedId;
    if (!docId) return;
    try {
      await downloadExport(projectId, docId, doc.document?.name ?? "export");
    } catch (err) {
      // Routed to the SAME surface every other failure in this shell uses.
      // An empty catch here left a failed export (a 413 over the size cap, a
      // 5xx, a dropped connection) with no surface at all: the control simply
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
    // item is disabled rather than triggering a compile.
    if (!compile.pdfBytes) return;
    const name = doc.document?.name ?? "document";
    saveBlob(new Blob([compile.pdfBytes.slice()], { type: "application/pdf" }), `${name}.pdf`);
  }

  const [deleteOpen, setDeleteOpen] = useState(false);
  const [renameOpen, setRenameOpen] = useState(false);
  const [renameBusy, setRenameBusy] = useState(false);
  const [renameError, setRenameError] = useState<string | null>(null);

  /**
   * Rename the open document from its own header.
   *
   * A collision is reported INLINE here rather than handed to
   * `ConflictDialog`: there is exactly one name in question and the field
   * to fix it is already on screen, so swapping in a second dialog to ask
   * the same question would take the field away in order to offer it back.
   */
  async function handleRenameDocument(name: string) {
    const docId = doc.selectedId;
    if (!docId) return;
    setRenameBusy(true);
    setRenameError(null);
    try {
      await doc.renameDoc(docId, name);
      setRenameOpen(false);
    } catch (err) {
      if (err instanceof NameCollisionError) {
        setRenameError(`"${err.takenName}" is taken. Try "${err.suggestion}".`);
        return;
      }
      setRenameError(errorText(err));
    } finally {
      setRenameBusy(false);
    }
  }

  async function handleDeleteDocument() {
    const docId = doc.selectedId;
    setDeleteOpen(false);
    if (!docId) return;
    await doc.removeDoc(docId);
    router.push(routes.latex(projectId));
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
    return <div className="h-[75vh] animate-pulse rounded-lg bg-muted" />;
  }

  const activeMeta = activePath ? doc.files.find((f) => f.path === activePath) : undefined;
  /*
    BOTH signals, never `isTexPath` alone. The two answer DIFFERENT questions
    (see `isTexPath`'s own comment in `lib/latex-tree.ts`): `is_binary` is how
    the backend STORED the bytes, `isTexPath` is whether a human should be
    shown a text buffer. A `.bib`/`.sty`/`.bst` in latin-1 out of a real
    Overleaf or arXiv project decodes as binary and is stored that way, and so
    is every file uploaded through the file tree whatever its extension.
    `openFile` correctly skips the fetch and the buffer for such a path -- so
    routing on the extension alone rendered an EMPTY editor over it, and the
    first keystroke PUT that empty buffer through `write_text`, which sets
    `is_binary=False` and `blob=None`. The original bytes were gone
    permanently and silently. Do not re-simplify this to one test.
  */
  const activeIsSource = activePath !== null && isTexPath(activePath) && !activeMeta?.is_binary;
  const canDuplicate = canEdit && activeIsSource && doc.buffers[activePath] !== undefined;

  const showSource = viewMode !== "pdf";
  const showPdf = viewMode !== "source";
  const pctUsed = doc.maxBytes > 0 ? (doc.usedBytes / doc.maxBytes) * 100 : 0;

  const tree = (
    <FileTree
      nodes={buildTree(doc.files)}
      activePath={doc.activePath}
      mainPath={doc.mainPath}
      canEdit={canEdit}
      onOpen={(path) => void doc.openFile(path)}
      onNewFileIn={openNewFile}
      onDelete={(path) => void doc.removeFile(path)}
      onRename={(from, to) => void withConflicts((p) => doc.moveFile(from, p), to)}
      onRenameDir={(from, to) => void withConflicts((p) => doc.moveDir(from, p), to)}
      onSetMain={(path) => void doc.setMainPath(path)}
      onUpload={(path, data) => void withConflicts((p) => doc.uploadBinary(p, data), path)}
    />
  );

  // A label wrapping a hidden input, not a Button that opens a dialog: the
  // dialog's only job would be to show a "browse" link. Accepts ANY type --
  // a project needs figures, .bib, .sty and .cls at least as often as it
  // needs an archive -- and routes a .zip to the import flow.
  function uploadControl(withLabel: boolean) {
    return (
      <label
        title="Upload a file (a .zip is unpacked into this project)"
        aria-label={withLabel ? undefined : "Upload file"}
        className={cn(
          buttonVariants({ variant: "ghost", size: withLabel ? "sm" : "icon" }),
          withLabel && "gap-1.5",
          !canEdit && "pointer-events-none opacity-50"
        )}
      >
        <Upload className="size-3.5" aria-hidden />
        {withLabel && "Upload file"}
        <input
          type="file"
          className="hidden"
          disabled={!canEdit}
          onChange={(e) => {
            const file = e.target.files?.[0];
            // Cleared so picking the SAME file twice still fires `change` --
            // re-uploading a figure you just fixed is the common case, and
            // without this the second pick is silently ignored.
            e.target.value = "";
            if (file) handleAddFile(file);
          }}
        />
      </label>
    );
  }

  const main = (
    <div className="flex h-full min-h-0 flex-col">
      {showSource ? (
        <OpenTabs
          paths={doc.openPaths}
          activePath={doc.activePath}
          dirtyPaths={doc.dirtyPaths}
          onSelect={(path) => void doc.openFile(path)}
          onClose={doc.closeFile}
        />
      ) : null}
      <div className="flex min-h-0 flex-1">
        {/*
          Both panes are hidden with `display: none`, never unmounted, when
          the view mode leaves them out. CodeMirror's undo history, cursor and
          scroll position live in the editor instance, and pdf.js re-parses
          the document on mount -- so unmounting would make every flip
          through the view modes cost the user real state and the browser a
          full re-render of the PDF.
        */}
        <div
          className={
            !showSource
              ? "hidden"
              : showPdf
                ? "flex min-h-0 w-1/2 min-w-0 flex-col border-r"
                : "flex min-h-0 min-w-0 flex-1 flex-col"
          }
        >
          {activePath === null ? (
            <div className="flex flex-1 items-center justify-center text-[13px] text-muted-foreground">
              Choose a file to edit
            </div>
          ) : activeIsSource ? (
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
              documentId={doc.selectedId ?? documentId}
              path={activePath}
              sizeBytes={activeMeta?.size_bytes ?? 0}
            />
          )}
        </div>
        <div
          className={
            !showPdf
              ? "hidden"
              : showSource
                ? "flex min-h-0 w-1/2 min-w-0 flex-col"
                : "flex min-h-0 min-w-0 flex-1 flex-col"
          }
        >
          <PdfViewer
            bytes={compile.pdfBytes}
            status={compile.status}
            wide={!showSource}
            highlight={compile.highlight}
            scrollToPage={compile.scrollToPage}
            onPageDoubleClick={(page, point) => void compile.jumpToSource(page, point)}
          />
        </div>
      </div>
      <LogPanel
        status={compile.status}
        log={compile.status === "failed" ? (compile.log?.text ?? null) : compile.buildLog}
        errorFile={compile.log?.file ?? null}
        errorLine={compile.log?.line ?? null}
        onJumpToError={handleJumpToError}
        note={compile.syncNote}
      />
    </div>
  );

  return (
    <div className="space-y-2">
      {/*
        A save failure is a fact about the user's TEXT, not about which
        document happens to be on screen -- it must survive a switch away
        from the document that failed, so it is rendered here, unconditionally,
        rather than folded into any per-document badge. No retry control: the
        text lives in a buffer the user may have navigated away from, and
        re-sending it over newer server state is a worse bug than the one
        this surfaces.
      */}
      {doc.saveFailures.map((f) => (
        <div
          // Keyed on BOTH halves of the record's identity: two files in the
          // same document can be failing at once, and an id-only key
          // collides between them.
          key={`${f.id}\u0000${f.path}`}
          role="alert"
          className="flex items-center justify-between gap-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-1.5 text-[12px] text-destructive"
        >
          {/* Names the FILE as well as the document: a document-level
              message could not tell the user which of several open files is
              the one still unsaved. */}
          <span>
            Changes to {f.path} in {f.name} could not be saved.
          </span>
          <button
            type="button"
            aria-label="Dismiss"
            onClick={() => doc.dismissSaveFailure(f.id, f.path)}
            className="text-destructive/70 hover:text-destructive"
          >
            <X className="size-3.5" aria-hidden />
          </button>
        </div>
      ))}
      {doc.error && (
        <div
          role="alert"
          className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-1.5 text-[12px] text-destructive"
        >
          {doc.error}
        </div>
      )}

      <div className="flex flex-col overflow-hidden rounded-lg border" style={{ height: "75vh" }}>
        <EditorToolbar
          projectId={projectId}
          name={doc.document?.name ?? "…"}
          mainPath={doc.mainPath ?? ""}
          engine={doc.engine}
          canEdit={canEdit}
          viewMode={viewMode}
          onViewModeChange={setViewMode}
          compiling={compile.compiling}
          onCompile={() => void compile.compile()}
          pdfStale={canEdit && compile.pdfBytes !== null && compile.stale && !compile.compiling}
          onShare={() => setShareOpen(true)}
          onRename={() => {
            setRenameError(null);
            setRenameOpen(true);
          }}
          onDelete={() => setDeleteOpen(true)}
          onEngineChange={(engine) => void doc.setEngine(engine)}
          canDownloadPdf={compile.pdfBytes !== null}
          onDownloadPdf={handleDownloadPdf}
          onExport={() => void handleExport()}
        />

        {/* `selectedId` trails the route by one render (the hook mirrors the
            prop in an effect), so this is a transient state, not the "no
            document" case the list page owns. */}
        {doc.selectedId === null ? (
          <div className="min-h-0 flex-1 animate-pulse bg-muted" />
        ) : isDesktop ? (
          <ResizablePanelGroup orientation="horizontal" className="hidden min-h-0 flex-1 md:flex">
            <ResizablePanel defaultSize={20} minSize={14} maxSize={32}>
              <div className="flex h-full flex-col">
                <div className="flex items-center gap-1 border-b p-1.5">
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label="New file"
                    disabled={!canEdit}
                    onClick={() => openNewFile("")}
                  >
                    <FilePlus2 className="size-3.5" aria-hidden />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label="Import zip"
                    disabled={!canEdit}
                    onClick={() => setImportOpen(true)}
                  >
                    <UploadCloud className="size-3.5" aria-hidden />
                  </Button>
                  {uploadControl(false)}
                </div>
                <div className="min-h-0 flex-1 overflow-y-auto">
                  {tree}
                  {canDuplicate && (
                    <div className="px-2 pb-2">
                      <Button variant="ghost" size="sm" className="text-[11px]" onClick={handleDuplicate}>
                        Duplicate current file
                      </Button>
                    </div>
                  )}
                </div>
                {/*
                  Always visible, not just above some threshold: a cap the
                  user can't see is a cap they hit as an unexplained failure
                  -- this footer is the reason the tree endpoint returns these
                  two numbers at all.
                */}
                <div className="border-t px-3 py-2">
                  <Progress
                    value={Math.min(pctUsed, 100)}
                    aria-label="Project storage used"
                    className={cn("h-1", pctUsed >= 90 && "bg-destructive/20 [&>div]:bg-destructive")}
                  />
                  <p
                    className={cn(
                      "mt-1.5 text-[11px]",
                      pctUsed >= 90 ? "font-medium text-destructive" : "text-muted-foreground"
                    )}
                  >
                    {formatBytes(doc.usedBytes)} of {formatBytes(doc.maxBytes)}
                  </p>
                </div>
              </div>
            </ResizablePanel>
            <ResizableHandle withHandle />
            <ResizablePanel defaultSize={80}>{main}</ResizablePanel>
          </ResizablePanelGroup>
        ) : (
          <div className="flex min-h-0 flex-1 flex-col md:hidden">
            <div className="flex items-center gap-1 border-b p-1.5">
              <Button
                variant="ghost"
                size="sm"
                className="gap-1.5"
                disabled={!canEdit}
                onClick={() => openNewFile("")}
              >
                <FilePlus2 className="size-3.5" aria-hidden /> New file
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className="gap-1.5"
                disabled={!canEdit}
                onClick={() => setImportOpen(true)}
              >
                <UploadCloud className="size-3.5" aria-hidden /> Import zip
              </Button>
              {uploadControl(true)}
            </div>
            <div className="max-h-40 overflow-y-auto border-b">{tree}</div>
            {main}
          </div>
        )}
      </div>

      <DocumentShareDialog
        open={shareOpen}
        onOpenChange={setShareOpen}
        projectId={projectId}
        documentId={documentId}
        canEdit={canEdit}
        fullAccessUserIds={[ownerId, doc.document?.created_by ?? null].filter(
          (id): id is string => id !== null
        )}
      />

      <EditorNewFileDialog
        open={newFile.open}
        initialValue={newFile.initial}
        busy={newFileBusy}
        error={newFileError}
        onOpenChange={(open) => {
          setNewFile((s) => ({ ...s, open }));
          if (!open) setNewFileError(null);
        }}
        onCreate={(path) => void handleCreateFile(path)}
      />

      <ImportDropzone
        open={importOpen}
        projectId={projectId}
        documentId={documentId}
        takenPaths={doc.files.map((f) => f.path)}
        takenNames={doc.documents.map((d) => d.name)}
        initialFile={importFile}
        onClose={() => {
          setImportOpen(false);
          setImportFile(null);
        }}
        onDone={(result, mode) => {
          setImportOpen(false);
          setImportFile(null);
          if (mode === "merge") {
            // The files landed in the document already on screen -- refresh
            // its tree, and stay exactly where the user was. Navigating
            // would be navigating to the page they are on. The commit's own
            // `revision` goes with it: the merge bumped it server-side, and
            // without folding it in the pre-import PDF stays marked current.
            void doc.refreshFiles(result.revision);
            return;
          }
          void doc.adoptDocument(result.id);
          router.push(routes.latexDoc(projectId, result.id));
        }}
      />

      <ConflictDialog
        open={conflict !== null}
        busy={conflictBusy}
        title="That name is already taken"
        description="A file with this name is already in the project. Choose a name to use instead."
        collisions={conflict?.collisions ?? []}
        taken={doc.files.map((f) => f.path)}
        onCancel={() => setConflict(null)}
        onConfirm={(decisions) => void confirmConflict(decisions)}
      />

      <EditorRenameDialog
        open={renameOpen}
        currentName={doc.document?.name ?? ""}
        busy={renameBusy}
        error={renameError}
        onOpenChange={(open) => {
          setRenameOpen(open);
          if (!open) setRenameError(null);
        }}
        onRename={(value) => void handleRenameDocument(value)}
      />

      <EditorDeleteDialog
        open={deleteOpen}
        docName={doc.document?.name ?? "This document"}
        onOpenChange={setDeleteOpen}
        onConfirm={() => void handleDeleteDocument()}
      />
    </div>
  );
}
