"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, Play } from "lucide-react";
import { Button } from "@/components/ui/button";
import { DocumentList } from "@/components/latex/document-list";
import { EditorPane } from "@/components/latex/editor-pane";
import { LogPanel } from "@/components/latex/log-panel";
import { PdfViewer, type PdfHighlight } from "@/components/latex/pdf-viewer";
import {
  compileDocument,
  createDocument,
  deleteDocument,
  fetchPdfBytes,
  getDocument,
  listDocuments,
  patchDocument,
  synctexForward,
  synctexReverse,
  type LatexDocument,
  type LatexEngine,
} from "@/lib/latex";
import { isStale, type CompiledState, type TexPoint } from "@/lib/latex-sync";
import type { Role } from "@/lib/types";

const CAN_EDIT: Role[] = ["owner", "editor"];
const AUTOSAVE_MS = 800;
const STALE_NOTE = "Out of date — compile to sync";

// Both sync directions answer for the LAST COMPILED source; after an
// edit the line numbers have drifted, so a jump is still shown but
// labelled stale rather than presented as confidently correct. Declared
// at MODULE level, beside AUTOSAVE_MS and STARTER -- not inside the
// component: a value created during render is a new binding every
// render, and react-hooks/exhaustive-deps would demand it in the
// dependency arrays of both jumpToPdf and jumpToSource below, defeating
// their memoisation.
const SYNC_STALE_NOTE = "The PDF is out of date, so this may be a few lines off.";

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

interface PendingSave {
  docId: string;
  text: string;
}

export function LatexWorkspace({ projectId, role }: LatexWorkspaceProps) {
  const canEdit = CAN_EDIT.includes(role);

  const [documents, setDocuments] = useState<LatexDocument[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [source, setSource] = useState("");
  const [loading, setLoading] = useState(true);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "error">("idle");
  const [splitPercent, setSplitPercent] = useState(50);
  const [compiled, setCompiled] = useState<CompiledState | null>(null);
  const [pdfBytes, setPdfBytes] = useState<Uint8Array | null>(null);
  const [log, setLog] = useState<string | null>(null);
  const [compiling, setCompiling] = useState(false);
  const [engine, setEngine] = useState<LatexEngine>("pdflatex");
  const [highlight, setHighlight] = useState<PdfHighlight | null>(null);
  const [scrollToPage, setScrollToPage] = useState<number | null>(null);
  const [gotoLine, setGotoLine] = useState<{ line: number; nonce: number } | null>(
    null
  );
  const [syncNote, setSyncNote] = useState<string | null>(null);
  // Monotonic, so jumping twice to the same line still scrolls the editor.
  const nonce = useRef(0);

  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // The id of the document `source` actually holds right now. A save must be
  // keyed to THIS, never to `selectedId` -- `selectedId` can flip to a new
  // document before that document's own text has actually loaded into the
  // buffer (getDocument is async), and a keystroke landing in that window
  // must still save against the document the buffer really contains.
  const bufferDocId = useRef<string | null>(null);
  // Last-known-server text, per document. The previous single string ref
  // can only describe ONE document at a time -- the moment a second
  // document enters play, which switching always does, it has nothing to
  // compare that document's own flushed save against. Keyed by id so every
  // document keeps its own baseline.
  const savedSourceByDoc = useRef<Map<string, string>>(new Map());
  // The most recent not-yet-sent edit, bound to the document it was typed
  // into at schedule time. `scheduleSave` is the only writer; `flush` is
  // the only reader.
  const pending = useRef<PendingSave | null>(null);

  // Mirrors `selectedId`, updated right here in the render body (not from an
  // effect, which would land one render late) so it is always exactly as
  // fresh as `selectedId` itself. `compile`'s completion guard needs to ask
  // "is the user still on this document?" -- that IS `selectedId`, updated
  // SYNCHRONOUSLY the instant the user picks something else. It is a
  // different question from `bufferDocId`'s "does the loaded buffer belong
  // to this document?", which only moves once that document's own
  // `getDocument` resolves -- ASYNCHRONOUSLY, and on a delay that has
  // nothing to do with whether the user is still looking at it. Selecting a
  // document and hitting Compile before its own load finishes -- unlikely,
  // since a container compile should comfortably outlast a metadata GET,
  // but neither the button nor Cmd/Ctrl+S is gated on the load completing --
  // would otherwise compare the compile's own result against a
  // `bufferDocId` that has not caught up yet, silently discarding a result
  // that belongs to exactly the document on screen. Do not "simplify" this
  // back to `bufferDocId` for consistency with `flush`: `flush` is correct
  // to use `bufferDocId`, because for a save "which document does this text
  // belong to" genuinely IS the buffer question. The two guards ask
  // different questions on purpose.
  const selectedIdRef = useRef(selectedId);
  selectedIdRef.current = selectedId;

  // Declared HERE, above every callback, and not lower down beside the JSX.
  // Task 6's sync callbacks list `stale` in their dependency ARRAY, which is
  // evaluated the moment useCallback runs -- a `const` declared further down
  // the component body is still in its temporal dead zone at that point and
  // throws a ReferenceError on first render.
  const stale = isStale(source, engine, compiled);

  // Sends `pending` right now, bypassing the debounce. A single timer/string
  // ref cannot represent two documents at once, so switching documents while
  // an edit is still waiting out its debounce must FLUSH that edit, not
  // just clear the timer: clearing silently dropped it (a document's worth
  // of typing, gone, no error shown), and leaving the old timer running
  // would instead fire it later under whatever document is current BY THEN,
  // misattributing it. Flushing sends it synchronously, against the
  // document it was actually typed into, before that document stops being
  // "current" in any sense.
  //
  // Returns the in-flight save as a Promise (rather than firing the PATCH
  // and forgetting it, as before) so `compile` below can await it: every
  // existing call site (the debounce timeout, the document-switch cleanup)
  // still just calls `flush()` and ignores the return value, unchanged.
  const flush = useCallback(async () => {
    if (saveTimer.current) {
      clearTimeout(saveTimer.current);
      saveTimer.current = null;
    }
    const toSend = pending.current;
    if (!toSend) return;
    pending.current = null;
    if (toSend.text === savedSourceByDoc.current.get(toSend.docId)) return;
    setSaveState("saving");
    try {
      await patchDocument(projectId, toSend.docId, { source: toSend.text });
      savedSourceByDoc.current.set(toSend.docId, toSend.text);
      setSaveState("idle");
    } catch {
      setSaveState("error");
    }
  }, [projectId]);

  useEffect(() => {
    let cancelled = false;
    listDocuments(projectId)
      .then((docs) => {
        if (cancelled) return;
        setDocuments(docs);
        setSelectedId((current) => current ?? docs[0]?.id ?? null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  useEffect(() => {
    // A's "Could not save" (or a "Saving..." that resolves mid-switch) must
    // not keep showing once B is on screen and was never touched -- reset
    // unconditionally on every selection change, before the new document
    // (if any) has even started loading.
    setSaveState("idle");
    if (!selectedId) {
      setSource("");
      bufferDocId.current = null;
      return;
    }
    let cancelled = false;
    getDocument(projectId, selectedId).then((doc) => {
      if (cancelled) return;
      setSource(doc.source);
      setEngine(doc.engine);
      setCompiled(null);
      setPdfBytes(null);
      setLog(null);
      setHighlight(null);
      setScrollToPage(null);
      setGotoLine(null);
      setSyncNote(null);
      bufferDocId.current = doc.id;
      savedSourceByDoc.current.set(doc.id, doc.source);
    });
    return () => {
      cancelled = true;
      // Runs before the next document's effect body -- i.e. exactly when
      // the buffer is about to stop belonging to this document. See flush's
      // own comment for why this must flush rather than just clear.
      flush();
    };
  }, [projectId, selectedId, flush]);

  // Autosave: debounced, and deliberately independent of compiling. Saving
  // preserves work; compiling costs a container run. Tying them together
  // would either lose edits or queue seconds-long runs behind every pause.
  const scheduleSave = useCallback(
    (next: string) => {
      if (!canEdit || !bufferDocId.current) return;
      // Loading a document redispatches its text into CodeMirror, which
      // reports it as an ordinary change and calls back in here -- that
      // redispatch is a normal consequence of setSource, not an edge case,
      // and it happens on every load, not just a slow one. If a DIFFERENT
      // document's edit is still sitting in `pending` at that moment,
      // overwriting it here would drop it exactly the way a bare
      // clearTimeout used to, so any call that targets a document other
      // than the one already pending must flush that entry first -- the
      // same "switch flushes, never just clears" rule the effect cleanup
      // already follows, applied to the one path that reaches
      // scheduleSave directly instead of going through it.
      if (pending.current && pending.current.docId !== bufferDocId.current) {
        flush();
      }
      pending.current = { docId: bufferDocId.current, text: next };
      if (saveTimer.current) clearTimeout(saveTimer.current);
      saveTimer.current = setTimeout(flush, AUTOSAVE_MS);
    },
    [canEdit, flush]
  );

  useEffect(() => {
    return () => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
    };
  }, []);

  const handleChange = useCallback(
    (next: string) => {
      setSource(next);
      scheduleSave(next);
    },
    [scheduleSave]
  );

  const compile = useCallback(async () => {
    if (!canEdit || !selectedId || compiling) return;
    // Captured now, before any await, and compared against `selectedIdRef`
    // -- NOT `bufferDocId` -- on every path below that writes
    // pdfBytes/compiled/log. See `selectedIdRef`'s own comment for why the
    // two guards in this file deliberately use different refs. Without this
    // guard at all, a slow compile of A completing after the user has
    // switched to B unconditionally overwrites B's screen with A's PDF or
    // log: the same failure family autosave was hardened against twice.
    // `isStale` does NOT catch this -- two untouched documents both created
    // from STARTER have identical source and engine, so A's PDF rendered
    // under B reports itself as perfectly up to date, with no signal
    // anything is wrong. An id comparison (not a sequence counter) is
    // deliberate: switching away from A and back to A before this resolves
    // must still apply the result, and A's id is the same both times.
    const docId = selectedId;
    setCompiling(true);
    try {
      // Flush a pending autosave first: the backend compiles the SAVED source,
      // so compiling mid-debounce would build the previous keystroke's text
      // and hand back a SyncTeX map that does not match what is on screen.
      // This is the same `flush` the document-switch cleanup uses -- no
      // second save path.
      await flush();

      const result = await compileDocument(projectId, docId);
      if (selectedIdRef.current !== docId) return; // user moved on; discard

      if (!result.ok || !result.pdf_hash) {
        // The last good PDF stays on screen on purpose: a broken edit should
        // not blank the preview, and the previous render is still the most
        // useful thing available.
        setLog(result.log);
        return;
      }
      const bytes = await fetchPdfBytes(projectId, docId, result.pdf_hash);
      if (selectedIdRef.current !== docId) return; // user moved on; discard

      setPdfBytes(bytes);
      setCompiled({ source, engine, hash: result.pdf_hash });
      setLog(null);
    } catch {
      if (selectedIdRef.current === docId) {
        setLog("The LaTeX compiler is unavailable. Please try again.");
      }
    } finally {
      setCompiling(false);
    }
  }, [canEdit, compiling, engine, flush, projectId, selectedId, source]);

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        void compile();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [compile]);

  // Both directions answer for the LAST COMPILED source. After an edit the
  // line numbers have drifted, so the result is still shown -- it is usually
  // close -- but labelled stale. Silently jumping to a confidently wrong line
  // is the failure the spec singles out as worse than admitting the map is old.
  //
  // Guard: `docId` is captured before the `await`, exactly like `compile`'s
  // own guard (see `selectedIdRef`'s comment above for why that ref, not
  // `bufferDocId`, is the right one here) -- "is the user still on this
  // document" is a question about the SELECTION, not the editor buffer, and
  // it must be re-asked after every await, before any state write. Without
  // this, a slow SyncTeX lookup for document A resolving after the user has
  // switched to document B would scroll B's viewer to a position from A, or
  // select a line in a file the user is no longer looking at -- the same
  // failure family `compile` and `flush` were both hardened against.
  const jumpToPdf = useCallback(
    async (line: number) => {
      if (!selectedId || !compiled) return;
      const docId = selectedId;
      try {
        const res = await synctexForward(projectId, docId, line);
        if (selectedIdRef.current !== docId) return; // user moved on; discard
        if (!res.found || res.page === null || res.x === null || res.y === null) {
          setSyncNote("No place in the PDF matches that line.");
          return;
        }
        setHighlight({
          page: res.page,
          x: res.x,
          y: res.y,
          width: res.width ?? 0,
          height: res.height ?? 0,
        });
        setScrollToPage(res.page);
        setSyncNote(stale ? SYNC_STALE_NOTE : null);
      } catch {
        // Navigation is an enhancement. A failed query leaves a working
        // editor and a working viewer.
        if (selectedIdRef.current === docId) {
          setSyncNote("Sync is unavailable right now.");
        }
      }
    },
    [compiled, projectId, selectedId, stale]
  );

  const jumpToSource = useCallback(
    async (page: number, point: TexPoint) => {
      if (!selectedId || !compiled) return;
      const docId = selectedId;
      try {
        const res = await synctexReverse(projectId, docId, page, point.x, point.y);
        if (selectedIdRef.current !== docId) return; // user moved on; discard
        if (!res.found || res.line === null) {
          setSyncNote("No source line matches that spot.");
          return;
        }
        setGotoLine({ line: res.line, nonce: ++nonce.current });
        setSyncNote(stale ? SYNC_STALE_NOTE : null);
      } catch {
        if (selectedIdRef.current === docId) {
          setSyncNote("Sync is unavailable right now.");
        }
      }
    },
    [compiled, projectId, selectedId, stale]
  );

  async function handleCreate(name: string) {
    const doc = await createDocument(projectId, { name, source: STARTER });
    setDocuments((prev) => [...prev, doc]);
    setSelectedId(doc.id);
  }

  // The engine is persisted here, explicitly, rather than from an effect
  // watching `engine`. An effect can't tell a user's choice apart from the
  // `setEngine(doc.engine)` that runs while loading a different document --
  // on that render `engine` still holds the PREVIOUS document's value, so an
  // effect would PATCH the newly-opened document to the old one's engine.
  function handleEngineChange(next: LatexEngine) {
    setEngine(next);
    if (!canEdit || !selectedId) return;
    void patchDocument(projectId, selectedId, { engine: next }).then((doc) =>
      setDocuments((prev) => prev.map((d) => (d.id === doc.id ? doc : d)))
    );
  }

  async function handleDelete(id: string) {
    // A pending save for this document must never be allowed to fire after
    // it's gone -- the PATCH would 404, surfacing as the stale "Could not
    // save" error banner (see the reset above) under whatever document is
    // selected by the time it fails. The load effect's own flush-on-switch
    // can't help here: it only runs on the NEXT selection change, which is
    // too late if a switch doesn't happen to follow.
    if (pending.current?.docId === id) {
      pending.current = null;
      if (saveTimer.current) {
        clearTimeout(saveTimer.current);
        saveTimer.current = null;
      }
    }
    await deleteDocument(projectId, id);
    setDocuments((prev) => prev.filter((d) => d.id !== id));
    setSelectedId((current) => (current === id ? null : current));
  }

  // Drag handle. Clamped so neither pane can be dragged out of existence.
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
  // mid-drag) would otherwise leak its listeners forever -- nothing else
  // is left to ever call `stop` for it.
  useEffect(() => {
    return () => {
      dragCleanup.current?.();
    };
  }, []);

  if (loading) {
    return <div className="h-[70vh] animate-pulse rounded-xl bg-muted" />;
  }

  return (
    <div className="flex h-[calc(100vh-14rem)] min-h-[32rem] gap-3">
      <DocumentList
        documents={documents}
        selectedId={selectedId}
        canEdit={canEdit}
        onSelect={setSelectedId}
        onCreate={handleCreate}
        onDelete={handleDelete}
      />

      {selectedId === null ? (
        <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
          Select a document, or create one to start writing.
        </div>
      ) : (
        <div className="relative flex flex-1 overflow-hidden rounded-xl border border-border">
          <div style={{ width: `${splitPercent}%` }} className="flex flex-col overflow-hidden">
            <div className="flex items-center justify-between border-b border-border px-3 py-1.5 text-xs text-muted-foreground">
              <span>Source</span>
              <span>
                {saveState === "saving" && "Saving…"}
                {saveState === "error" && (
                  <span className="text-destructive">Could not save</span>
                )}
              </span>
            </div>
            <div className="flex-1 overflow-hidden">
              <EditorPane
                value={source}
                onChange={handleChange}
                onLineDoubleClick={(line) => void jumpToPdf(line)}
                gotoLine={gotoLine}
                readOnly={!canEdit}
              />
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
                {pdfBytes && stale && (
                  <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-[11px] font-medium text-amber-700 dark:text-amber-400">
                    {STALE_NOTE}
                  </span>
                )}
                {syncNote && (
                  <span className="text-[11px] text-muted-foreground">{syncNote}</span>
                )}
              </div>
              <div className="flex items-center gap-2">
                <select
                  value={engine}
                  disabled={!canEdit}
                  onChange={(e) => handleEngineChange(e.target.value as LatexEngine)}
                  className="rounded-md border border-input bg-background px-1.5 py-0.5 text-[11px]"
                >
                  <option value="pdflatex">pdflatex</option>
                  <option value="xelatex">xelatex</option>
                </select>
                <Button
                  size="sm"
                  className="h-6 gap-1 px-2 text-[11px]"
                  disabled={!canEdit || compiling}
                  title={canEdit ? "Compile (Cmd/Ctrl+S)" : "You need editor access to compile"}
                  onClick={() => void compile()}
                >
                  {compiling ? (
                    <Loader2 className="size-3 animate-spin" />
                  ) : (
                    <Play className="size-3" />
                  )}
                  Compile
                </Button>
              </div>
            </div>
            <div className="flex-1 overflow-hidden">
              <PdfViewer
                bytes={pdfBytes}
                scale={1.25}
                highlight={highlight}
                scrollToPage={scrollToPage}
                onPageDoubleClick={(page, point) => void jumpToSource(page, point)}
              />
            </div>
            {log !== null && (
              <LogPanel
                log={log}
                onClose={() => setLog(null)}
                onJumpToLine={(line) => setGotoLine({ line, nonce: ++nonce.current })}
              />
            )}
          </div>
        </div>
      )}
    </div>
  );
}
