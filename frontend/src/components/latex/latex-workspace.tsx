"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Clock, Loader2, Play, X } from "lucide-react";
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
import { compileMeta } from "@/lib/latex-status";
import { buildTree, isBeneath, isTexPath, parentDir } from "@/lib/latex-tree";
import type { Role } from "@/lib/types";

const CAN_EDIT: Role[] = ["owner", "editor"];
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
    activePath: doc.activePath,
    beforeCompile,
  });

  // When a compile last LANDED IN THIS BROWSER. Nothing in the API carries a
  // built-at time -- `CompiledState` is a revision and a hash -- so this is
  // the only honest source for the header's "compiled 14:32", and it is
  // deliberately derived from `compile.compiled`'s identity rather than set
  // beside the compile call: the hook clears that state to null the instant
  // the selected document changes, and a timestamp kept anywhere else would
  // go on describing the previous document's build.
  const [compiledAt, setCompiledAt] = useState<number | null>(null);
  useEffect(() => {
    setCompiledAt(compile.compiled ? Date.now() : null);
  }, [compile.compiled]);

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
    return <div className="rx-tex-skel" />;
  }

  const activeMeta = activePath ? doc.files.find((f) => f.path === activePath) : undefined;
  const meta = compileMeta({
    engine: doc.engine,
    compiledAt,
    stale: compile.stale,
    compiling: compile.compiling,
  });
  const docName = doc.documents.find((d) => d.id === doc.selectedId)?.name ?? null;

  return (
    <div>
      <header className="rx-head">
        <div>
          <div className="rx-eyebrow">Manuscript</div>
          <h1>{docName ?? "No document yet"}</h1>
        </div>
        {doc.selectedId && (
          <div className="rx-meta">
            {meta.primary}
            {meta.secondary && (
              <>
                <br />
                {meta.secondary}
              </>
            )}
          </div>
        )}
      </header>

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
              className="rx-tex-fail"
            >
              {/* Names the FILE as well as the document: a document-level
                  message could not tell the user which of several open
                  files is the one still unsaved. */}
              <span>
                Changes to {f.path} in {f.name} could not be saved.
              </span>
              <button
                onClick={() => doc.dismissSaveFailure(f.id, f.path)}
                className="rx-icon-btn"
              >
                <X className="size-3.5" />
              </button>
            </div>
          ))}
        </div>
      )}

      <div className="rx-tex-bar">
          <select
            value={doc.selectedId ?? ""}
            onChange={(e) => doc.select(e.target.value || null)}
            aria-label="Document"
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
            />
          ) : (
            <button
              className="rx-btn rx-btn-ghost"
              disabled={!canEdit}
              title={canEdit ? "New document" : "You need editor access to add a document"}
              onClick={() => setCreatingDoc(true)}
            >
              New
            </button>
          )}

          <button
            className="rx-btn rx-btn-ghost"
            disabled={!canEdit || !doc.selectedId}
            title={canEdit ? "Delete document" : "You need editor access to delete a document"}
            onClick={() => doc.selectedId && void doc.removeDoc(doc.selectedId)}
          >
            Delete
          </button>

          {doc.selectedId && (
            <select
              value={doc.engine}
              disabled={!canEdit}
              aria-label="Compile engine"
              onChange={(e) => void doc.setEngine(e.target.value as LatexEngine)}
            >
              <option value="pdflatex">pdflatex</option>
              <option value="xelatex">xelatex</option>
            </select>
          )}

          {doc.error && <span className="rx-tex-err">{doc.error}</span>}
      </div>

      {doc.selectedId === null ? (
        <div className="rx-tex-empty">
          <p>Select a document, or create one to start writing.</p>
        </div>
      ) : (
        <div className="rx-tex-grid">
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

          <div style={{ width: `${splitPercent}%` }} className="rx-pane">
            <OpenTabs
              paths={doc.openPaths}
              activePath={doc.activePath}
              dirtyPaths={doc.dirtyPaths}
              onSelect={(path) => void doc.openFile(path)}
              onClose={doc.closeFile}
            />
            <div className="rx-save-state">
              {doc.saveState === "saving" && "Saving…"}
              {doc.saveState === "error" && (
                <span style={{ color: "oklch(var(--destructive))" }}>Could not save</span>
              )}
            </div>
            <div className="rx-editor-host">
              {activePath === null ? (
                <div className="rx-pane-empty">Select or create a file to start writing.</div>
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
            className="rx-split"
            role="separator"
            aria-orientation="vertical"
          />

          <div className="rx-tex-preview">
            {/* The compile control lives HERE, in the preview's own bar, not
                in the document toolbar above: it is the control that changes
                what this pane shows, and the staleness badge beside it is a
                statement about this pane's contents. Nothing about what it
                does moved with it -- the same `compile.compile()`, which
                still flushes every pending save first. */}
            <div className="rx-tex-preview-bar">
              <button
                className="rx-btn"
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
              </button>
              {compile.pdfBytes && compile.stale && (
                <span className="rx-stale">
                  <Clock className="size-2.5" />
                  {STALE_NOTE}
                </span>
              )}
              {/* `compile.syncNote` is already gated inside the hook: the
                  "PDF is out of date" message can never render while the
                  PDF is not, in fact, out of date. Every OTHER message
                  (declined-sync, "no place matches", "unavailable") passes
                  through unconditionally. */}
              {compile.syncNote && <span className="rx-sync-note">{compile.syncNote}</span>}
            </div>
            <div className="rx-tex-preview-body">
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
