"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { PdfHighlight } from "@/components/latex/pdf-viewer";
import {
  compileDocument,
  fetchPdfBytes,
  synctexForward,
  synctexReverse,
  PdfNotFoundError,
} from "@/lib/latex";
import { isStale, type CompiledState, type TexPoint } from "@/lib/latex-sync";

// Both sync directions answer for the LAST COMPILED source; after an edit
// the line numbers have drifted, so a jump is still shown but labelled
// stale rather than presented as confidently correct. Declared at MODULE
// level, not inside the hook: a value created during render is a new
// binding every render, and react-hooks/exhaustive-deps would demand it in
// the dependency arrays of both jumpToPdf and jumpToSource below, defeating
// their memoisation. Copied verbatim from the file this hook replaces.
const SYNC_STALE_NOTE = "The PDF is out of date, so this may be a few lines off.";

export interface UseLatexCompile {
  compiling: boolean;
  compiled: CompiledState | null;
  pdfBytes: Uint8Array | null;
  log: string | null;
  setLog: (log: string | null) => void;
  highlight: PdfHighlight | null;
  scrollToPage: number | null;
  gotoLine: { line: number; nonce: number } | null;
  jumpToLine: (line: number) => void;
  syncNote: string | null;
  stale: boolean;
  compile: () => Promise<void>;
  jumpToPdf: (line: number, file: string) => Promise<void>;
  jumpToSource: (page: number, point: TexPoint) => Promise<void>;
}

export function useLatexCompile(args: {
  projectId: string;
  documentId: string | null;
  revision: number | null;
  canEdit: boolean;
  isDirty: () => boolean;
  flushAll: () => Promise<void>;
  /** Called with the file a reverse-sync hit names, before the line jump. */
  onOpenFile: (path: string) => Promise<void>;
}): UseLatexCompile {
  const { projectId, documentId, revision, canEdit, isDirty, flushAll, onOpenFile } = args;

  const [compiling, setCompiling] = useState(false);
  const [compiled, setCompiled] = useState<CompiledState | null>(null);
  const [pdfBytes, setPdfBytes] = useState<Uint8Array | null>(null);
  const [log, setLog] = useState<string | null>(null);
  const [highlight, setHighlight] = useState<PdfHighlight | null>(null);
  const [scrollToPage, setScrollToPage] = useState<number | null>(null);
  const [gotoLine, setGotoLine] = useState<{ line: number; nonce: number } | null>(
    null
  );
  const [syncNote, setSyncNote] = useState<string | null>(null);

  // Monotonic, so jumping twice to the same line still scrolls the editor.
  const nonce = useRef(0);

  // Mirrors `documentId`, updated right here in the render body (not from an
  // effect, which would land one render late) so it is always exactly as
  // fresh as `documentId` itself. Every `await` below is followed by
  // comparing against THIS ref, never `documentId` from the closure -- the
  // closure's value is frozen at the moment the async callback was invoked,
  // while a switch away and back needs the CURRENT selection at the moment
  // each await resolves. An id comparison, not a sequence counter, is
  // deliberate: switching away from A and back to A before a compile or a
  // sync query resolves must still apply the result, since A's id is the
  // same both times a sequence counter would not be.
  //
  // `isStale` does NOT catch a cross-document mix-up on its own: two
  // untouched documents created from the same starter template have
  // identical source and engine, so A's PDF rendered under B would report
  // itself as perfectly up to date. This ref is what actually stops that.
  const documentIdRef = useRef(documentId);
  documentIdRef.current = documentId;

  // Declared HERE, above every callback below that lists it in a dependency
  // array. A `const` declared further down the hook body is still in its
  // temporal dead zone at the moment `useCallback` evaluates that array,
  // and throws a ReferenceError on first render -- exactly the failure the
  // file this hook replaces hit once and left a comment about.
  const stale = isStale(isDirty(), revision, compiled);

  const compile = useCallback(async () => {
    if (!canEdit || !documentId || compiling) return;
    // Captured now, before any await. See `documentIdRef`'s own comment
    // above for why every subsequent check compares against THAT ref and
    // not this closed-over value.
    const docId = documentId;
    setCompiling(true);
    try {
      // The backend compiles the SAVED tree, plural -- every open buffer,
      // not just the active one. Compiling mid-debounce would build the
      // previous keystroke's text (in whichever files were dirty) and hand
      // back a SyncTeX map that does not match what is on screen.
      await flushAll();
      if (documentIdRef.current !== docId) return; // user moved on; discard

      const result = await compileDocument(projectId, docId);
      if (documentIdRef.current !== docId) return; // user moved on; discard

      // `result.revision` is typed nullable on the wire, but a genuine
      // success always carries one -- the backend has nothing else to
      // report the build's revision as. Treating a missing revision as a
      // failure (rather than inventing a fallback number) keeps `compiled`
      // an honest record of what the server actually said it built, and
      // routes through the exact same "leave the last good PDF alone" path
      // as every other compile failure below.
      if (!result.ok || !result.pdf_hash || result.revision === null) {
        // The last good PDF stays on screen on purpose: a broken edit
        // should not blank the preview, and the previous render is still
        // the most useful thing available.
        setLog(result.log);
        return;
      }

      let bytes: Uint8Array;
      try {
        bytes = await fetchPdfBytes(projectId, docId, result.pdf_hash);
      } catch (err) {
        // The compiler just reported success and handed back a hash, but
        // the build is gone by the time this fetch runs -- the cache
        // evicted it, not a compiler outage. "Unavailable" would tell the
        // user to wait for something that was never coming back on its
        // own; recompiling produces a fresh hash and a fresh file
        // immediately.
        if (documentIdRef.current === docId) {
          setLog(
            err instanceof PdfNotFoundError
              ? "That build is no longer cached. Compile again to rebuild it."
              : "The LaTeX compiler is unavailable. Please try again."
          );
        }
        return;
      }
      if (documentIdRef.current !== docId) return; // user moved on; discard

      setPdfBytes(bytes);
      setCompiled({ revision: result.revision, hash: result.pdf_hash });
      setLog(null);
      // A fresh PDF invalidates both: `highlight` marks coordinates in the
      // PREVIOUS build, meaningless against this one, and `syncNote` may
      // still be the "PDF is out of date" message from a jump made while
      // stale -- which this compile, having just succeeded, made false.
      // Its render is ALSO gated on `stale` directly wherever this hook's
      // consumer shows it, for the same invariant, but clearing here means
      // it does not linger even for the one render before that recomputes.
      setHighlight(null);
      setSyncNote(null);
    } catch {
      if (documentIdRef.current === docId) {
        setLog("The LaTeX compiler is unavailable. Please try again.");
      }
    } finally {
      setCompiling(false);
    }
  }, [canEdit, compiling, documentId, flushAll, projectId]);

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
  // line numbers have drifted, so the result is still shown -- it is
  // usually close -- but labelled stale. Silently jumping to a confidently
  // wrong line is the failure the spec singles out as worse than admitting
  // the map is old.
  //
  // Guard: `docId` is captured before the `await`, exactly like `compile`'s
  // own guard above -- "is the user still on this document" must be
  // re-asked after every await, before any state write. Without this, a
  // slow SyncTeX lookup for document A resolving after the user has
  // switched to document B would scroll B's viewer to a position from A.
  const jumpToPdf = useCallback(
    async (line: number, file: string) => {
      if (!documentId || !compiled) return;
      const docId = documentId;
      try {
        // The ACTIVE file's tree-relative path, supplied by the caller --
        // this hook owns no notion of which file is open, that lives in
        // use-latex-document.
        const res = await synctexForward(projectId, docId, line, file);
        if (documentIdRef.current !== docId) return; // user moved on; discard
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
        if (documentIdRef.current === docId) {
          setSyncNote("Sync is unavailable right now.");
        }
      }
    },
    [compiled, documentId, projectId, stale]
  );

  const jumpToSource = useCallback(
    async (page: number, point: TexPoint) => {
      if (!documentId || !compiled) return;
      const docId = documentId;
      try {
        const res = await synctexReverse(projectId, docId, page, point.x, point.y);
        if (documentIdRef.current !== docId) return; // user moved on; discard
        // `found: false` and a null `file` mean the same thing -- the
        // backend already refuses to report a hit for a path not in the
        // tree -- so both are handled by the one branch below rather than
        // treating a present line with no file as a partial success.
        if (!res.found || res.line === null || res.file === null) {
          setSyncNote("No source line matches that spot.");
          return;
        }
        // Opens the file BEFORE jumping. Without awaiting this first, the
        // line lands in whatever buffer happened to be open at the time --
        // `setGotoLine` only means anything to whichever file is active
        // when the editor reads it.
        await onOpenFile(res.file);
        if (documentIdRef.current !== docId) return; // user moved on; discard
        setGotoLine({ line: res.line, nonce: ++nonce.current });
        setSyncNote(stale ? SYNC_STALE_NOTE : null);
      } catch {
        if (documentIdRef.current === docId) {
          setSyncNote("Sync is unavailable right now.");
        }
      }
    },
    [compiled, documentId, onOpenFile, projectId, stale]
  );

  // A synchronous jump with no network round trip -- e.g. LogPanel's
  // "jump to this line" on a compiler error. Distinct from `jumpToSource`,
  // which resolves a PDF coordinate through SyncTeX first; this just moves
  // the editor cursor to a line number the caller already has.
  const jumpToLine = useCallback((line: number) => {
    setGotoLine({ line, nonce: ++nonce.current });
  }, []);

  // The staleness message is withheld HERE, structurally, rather than left
  // to whatever JSX eventually renders `syncNote` -- the "PDF is out of
  // date" text must never render while the PDF is not, in fact, out of
  // date, regardless of whether some future call site remembers to gate
  // it. `syncNote` state is set once, at jump time, and persists across
  // renders; `stale` is recomputed on every render (a compile can land, or
  // an edit can happen, without `syncNote` itself changing) -- so this is
  // the freshest place to apply the gate, on every render, before the value
  // ever leaves the hook. Every OTHER message this state can hold ("No
  // place in the PDF matches...", "Sync is unavailable...") is not a
  // staleness claim and passes through unconditionally.
  const visibleSyncNote =
    syncNote && (syncNote !== SYNC_STALE_NOTE || stale) ? syncNote : null;

  return {
    compiling,
    compiled,
    pdfBytes,
    log,
    setLog,
    highlight,
    scrollToPage,
    gotoLine,
    jumpToLine,
    syncNote: visibleSyncNote,
    stale,
    compile,
    jumpToPdf,
    jumpToSource,
  };
}
