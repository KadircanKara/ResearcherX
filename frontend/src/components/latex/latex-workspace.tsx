"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  ArrowLeft,
  Code,
  Cog,
  Columns2,
  Download,
  FileDown,
  FileText,
  Loader2,
  PanelLeftOpen,
  Play,
  Trash2,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { BinaryPreview } from "@/components/latex/binary-preview";
import { ConflictDialog } from "@/components/latex/conflict-dialog";
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
  PathCollisionError,
  downloadExport,
  saveBlob,
  type LatexCollision,
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

type ViewMode = "split" | "code" | "pdf";

const VIEW_MODES: { mode: ViewMode; label: string; Icon: typeof Code }[] = [
  { mode: "code", label: "Source only", Icon: Code },
  { mode: "split", label: "Split source and PDF", Icon: Columns2 },
  { mode: "pdf", label: "PDF only", Icon: FileText },
];

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

  // Which panes are on screen. The editor and the preview are the only two
  // that answer to this -- the file tree collapses independently, because
  // "show me only the PDF" and "give the tree's width back" are different
  // requests and folding them together would make each imply the other.
  const [viewMode, setViewMode] = useState<ViewMode>("split");

  // Percent of the EDITOR+PREVIEW region (never of the whole row) taken by
  // the editor. The tree is outside that region and has its own width, so
  // this stays a true even split at 50 whatever the tree is doing -- the
  // previous percent-of-the-whole-row reading made the default silently
  // uneven, and made every tree resize move the seam.
  const [splitPercent, setSplitPercent] = useState(50);
  const [treeWidth, setTreeWidth] = useState(256);
  const [treeCollapsed, setTreeCollapsed] = useState(false);

  // The active drag's teardown, so an unmount mid-drag can still run it.
  const dragCleanup = useRef<(() => void) | null>(null);

  // Shared by both seams. The pointer-capture and teardown rules below are
  // subtle enough that a second copy of them would be a second place for
  // the pointercancel case to go missing; only the per-drag maths differs,
  // and that arrives as `onMove`.
  function beginDrag(
    e: React.PointerEvent<HTMLDivElement>,
    onMove: (ev: PointerEvent, hostBox: DOMRect) => void
  ) {
    const handle = e.currentTarget;
    const host = handle.parentElement;
    if (!host) return;
    const box = host.getBoundingClientRect();
    const pointerId = e.pointerId;
    // Capture so move/up/cancel keep reaching this element even once the
    // pointer leaves the 1.5px-wide handle -- true for any drag that moves
    // more than a few pixels, not an edge case.
    handle.setPointerCapture(pointerId);

    const move = (ev: PointerEvent) => onMove(ev, box);
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

  // Clamped so neither pane can be dragged out of existence.
  function startSplitDrag(e: React.PointerEvent<HTMLDivElement>) {
    beginDrag(e, (ev, box) => {
      const pct = ((ev.clientX - box.left) / box.width) * 100;
      setSplitPercent(Math.min(80, Math.max(20, pct)));
    });
  }

  // Clamped in pixels, not percent: the tree holds file names, whose
  // legibility has nothing to do with how wide the window is. The floor is
  // the point below which the header controls stop fitting; collapsing
  // entirely is the button's job, not the drag's.
  function startTreeDrag(e: React.PointerEvent<HTMLDivElement>) {
    beginDrag(e, (ev, box) => {
      setTreeWidth(Math.min(520, Math.max(180, ev.clientX - box.left)));
    });
  }

  // A drag in progress when the component unmounts (e.g. a route change
  // mid-drag) would otherwise leak its listeners forever -- nothing else is
  // left to ever call `stop` for it.
  useEffect(() => {
    return () => {
      dragCleanup.current?.();
    };
  }, []);

  // Import dialog: open state only. The two-step plan/commit conversation
  // (and its own busy/error) lives inside `ImportDropzone`, which the
  // projects list page shares.
  const [importOpen, setImportOpen] = useState(false);

  const [engineOpen, setEngineOpen] = useState(false);

  // The duplicate-name question for the TREE surfaces (new file, upload,
  // rename). `retry` is the same operation the user already asked for,
  // re-issued at whatever path they settle on -- never a second, different
  // call built from the collision.
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
            Three states, not a single "focus" toggle: a writer wants the
            source alone while drafting and the PDF alone while reading, and
            a toggle between "split" and one favoured pane cannot express
            both without a second control anyway.
          */}
          <div className="mr-1 flex items-center rounded-md border border-border p-0.5">
            {VIEW_MODES.map(({ mode, label, Icon }) => (
              <button
                key={mode}
                onClick={() => setViewMode(mode)}
                title={label}
                aria-label={label}
                aria-pressed={viewMode === mode}
                className={
                  viewMode === mode
                    ? "rounded-[3px] bg-muted p-1 text-foreground"
                    : "rounded-[3px] p-1 text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
                }
              >
                <Icon className="size-3.5" />
              </button>
            ))}
          </div>

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
              <Cog className="size-4" />
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
          {treeCollapsed ? (
            /* A rail, not nothing: with the tree gone there would otherwise
               be no control anywhere on screen to bring it back. */
            <div className="flex w-9 shrink-0 flex-col items-center border-r border-border pt-2">
              <Button
                size="icon-sm"
                variant="ghost"
                title="Show file tree"
                aria-label="Show file tree"
                onClick={() => setTreeCollapsed(false)}
              >
                <PanelLeftOpen className="size-3.5" />
              </Button>
            </div>
          ) : (
            <>
              <FileTree
                nodes={buildTree(doc.files)}
                width={treeWidth}
                activePath={doc.activePath}
                mainPath={doc.mainPath}
                canEdit={canEdit}
                usedBytes={doc.usedBytes}
                maxBytes={doc.maxBytes}
                error={doc.error}
                onOpen={(path) => void doc.openFile(path)}
                onCreate={(path) => void withConflicts((p) => doc.createFile(p), path)}
                onDelete={(path) => void doc.removeFile(path)}
                onRename={(from, to) => void withConflicts((p) => doc.moveFile(from, p), to)}
                onSetMain={(path) => void doc.setMainPath(path)}
                onUpload={(path, data) =>
                  void withConflicts((p) => doc.uploadBinary(p, data), path)
                }
                onImportClick={() => setImportOpen(true)}
                onExport={() => void handleExport()}
                onCollapse={() => setTreeCollapsed(true)}
              />
              {/* Sits OUTSIDE the tree so its drag maths reads the row's own
                  box -- `beginDrag` measures `parentElement`, and inside the
                  tree that would be the tree itself, which is the thing
                  being resized. */}
              <div
                onPointerDown={startTreeDrag}
                className="w-1.5 shrink-0 cursor-col-resize bg-border transition-colors hover:bg-primary/40"
                role="separator"
                aria-orientation="vertical"
                aria-label="Resize file tree"
              />
            </>
          )}

          {/* The editor+preview region. `min-w-0` is load-bearing on a flex
              child holding a horizontally-scrolling editor: without it the
              region refuses to shrink below its content and the tree's drag
              pushes the preview off-screen instead of narrowing anything. */}
          <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
            <div className="flex min-h-0 flex-1">
              {/*
                Hidden with `display: none`, never unmounted. CodeMirror's
                undo history, cursor and scroll position live in the editor
                instance, and pdf.js re-parses the document on mount -- so
                unmounting would make every flip through the view modes cost
                the user real state and the browser a full re-render of the
                PDF.
              */}
              <div
                style={viewMode === "split" ? { width: `${splitPercent}%` } : undefined}
                className={
                  viewMode === "pdf"
                    ? "hidden"
                    : viewMode === "code"
                      ? "flex min-w-0 flex-1 flex-col overflow-hidden"
                      : "flex min-w-0 shrink-0 flex-col overflow-hidden"
                }
              >
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

              {viewMode === "split" && (
                <div
                  onPointerDown={startSplitDrag}
                  className="w-1.5 shrink-0 cursor-col-resize bg-border transition-colors hover:bg-primary/40"
                  role="separator"
                  aria-orientation="vertical"
                  aria-label="Resize editor and preview"
                />
              )}

              <div
                className={
                  viewMode === "code"
                    ? "hidden"
                    : "flex min-w-0 flex-1 flex-col overflow-hidden"
                }
              >
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
              </div>
            </div>

            {/*
              Spans BOTH panes rather than living inside the preview, because
              a compile log is the only place a failed build explains itself
              -- inside the preview it would be invisible in source-only
              mode, which is exactly the mode someone fixing an error is in.
            */}
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
        projectId={projectId}
        documentId={documentId}
        takenPaths={doc.files.map((f) => f.path)}
        takenNames={doc.documents.map((d) => d.name)}
        onClose={() => setImportOpen(false)}
        onDone={(result, mode) => {
          setImportOpen(false);
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
          router.push(`/research/${projectId}/latex/${result.id}`);
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
    </div>
  );
}
