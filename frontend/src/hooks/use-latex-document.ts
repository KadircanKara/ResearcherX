"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  createDocument,
  deleteDocument,
  deleteFile,
  getDocument,
  listDocuments,
  listFiles,
  patchDocument,
  readTextFile,
  renameFile,
  writeBinaryFile,
  writeTextFile,
  PathCollisionError,
  errorText,
  type LatexDocument,
  type LatexEngine,
  type LatexFileMeta,
  type LatexMutation,
} from "@/lib/latex";
import { SaveEngine, type SaveState } from "@/lib/latex-buffers";

const AUTOSAVE_MS = 800;

export interface UseLatexDocument {
  documents: LatexDocument[];
  selectedId: string | null;
  select: (id: string | null) => void;
  document: LatexDocument | null;
  revision: number | null;
  mainPath: string | null;
  engine: LatexEngine;
  files: LatexFileMeta[];
  usedBytes: number;
  maxBytes: number;
  openPaths: string[];
  activePath: string | null;
  buffers: Record<string, string>;
  saveState: SaveState;
  /** Paths holding text the server does not, for the tab bar's dirty dot. */
  dirtyPaths: string[];
  /**
   * FILES whose autosave failed: the document id and name (what the user
   * recognises in a banner) plus the tree-relative path that actually
   * failed. Survives a switch away from the document that failed -- see
   * `saveFailures` state's own comment for why.
   */
  saveFailures: { id: string; path: string; name: string }[];
  /** Acknowledges one entry in `saveFailures`, removing it from the list. */
  dismissSaveFailure: (id: string, path: string) => void;
  /**
   * Routes a failure the SHELL owns (the export download, for one) through
   * this hook's single `error` surface, under the same 4xx-shows-the-server's
   * -detail / 5xx-shows-a-generic-line rule everything else here follows.
   */
  reportError: (err: unknown) => void;
  loading: boolean;
  /** Non-null only for a failure about the document as a whole. */
  error: string | null;
  isDirty: () => boolean;
  flushAll: () => Promise<void>;
  openFile: (path: string) => Promise<void>;
  closeFile: (path: string) => void;
  editBuffer: (path: string, text: string) => void;
  createFile: (path: string) => Promise<void>;
  removeFile: (path: string) => Promise<void>;
  moveFile: (from: string, to: string) => Promise<void>;
  uploadBinary: (path: string, data: Blob) => Promise<void>;
  setEngine: (engine: LatexEngine) => Promise<void>;
  /**
   * Awaits the in-flight `PATCH .../engine` for `docId`, if there is one --
   * so a racing `compile()` can build against the engine the user actually
   * picked rather than whatever the server still has. A patch for a
   * DIFFERENT document (the user has since switched away) is ignored: it has
   * no bearing on compiling this one. See `setEngine`'s own comment.
   */
  awaitEnginePatch: (docId: string) => Promise<void>;
  setMainPath: (path: string) => Promise<void>;
  createDoc: (name: string, source?: string) => Promise<void>;
  removeDoc: (id: string) => Promise<void>;
  /**
   * Re-lists the OPEN document's tree. Exposed for the one mutation this
   * hook does not itself perform: a committed merge import writes files
   * straight into the open document, so nothing here saw the write and the
   * tree on screen would otherwise keep describing the pre-import project.
   *
   * `revision` is the number the COMMIT response returned and must be passed
   * whenever the caller has one: a merge bumped `latex_documents.revision`
   * server-side, and `revision` is this app's only staleness signal (see the
   * `isStale` call in `use-latex-compile.ts`). Refreshing the tree alone
   * leaves the pre-import PDF marked CURRENT after N new files landed --
   * possibly the very chapter a `\input` names.
   */
  refreshFiles: (revision?: number) => Promise<void>;
  /**
   * Takes a document created OUTSIDE this hook (a committed "create" import)
   * into the list and selects it, so the caller does not have to re-list to
   * see it. The navigation is the caller's -- the route owns which document
   * is open (see `documentId`), and this hook does not route.
   */
  adoptDocument: (id: string) => Promise<void>;
}

/**
 * @param documentId When given, the ROUTE owns which document is open and this
 * hook stops choosing one: it neither defaults to the first document nor keeps
 * a selection the URL has moved away from. Omit it (or pass `undefined`) for
 * the older behaviour where the hook selects the first document itself.
 */
export function useLatexDocument(
  projectId: string,
  canEdit: boolean,
  documentId?: string | null
): UseLatexDocument {
  const [documents, setDocuments] = useState<LatexDocument[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // Named `documentState` internally purely to avoid shadowing the DOM
  // global `document` inside this file -- the returned object's key is
  // still exactly `document`, per the interface.
  const [documentState, setDocumentState] = useState<LatexDocument | null>(null);
  const [revision, setRevision] = useState<number | null>(null);
  const [mainPath, setMainPathState] = useState<string | null>(null);
  const [engine, setEngineState] = useState<LatexEngine>("pdflatex");
  const [files, setFiles] = useState<LatexFileMeta[]>([]);
  const [usedBytes, setUsedBytes] = useState(0);
  const [maxBytes, setMaxBytes] = useState(0);
  const [openPaths, setOpenPaths] = useState<string[]>([]);
  const [activePath, setActivePath] = useState<string | null>(null);
  const [buffers, setBuffers] = useState<Record<string, string>>({});
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [documentsLoading, setDocumentsLoading] = useState(true);
  // Read inside the list effect, which must NOT re-run when the route
  // changes document -- re-listing on every navigation would flash the
  // whole workspace through its loading state for data it already has.
  const documentIdRef = useRef(documentId);
  documentIdRef.current = documentId;
  const [docLoading, setDocLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /**
   * FILES whose autosave failed, keyed by document id AND tree-relative
   * path.
   *
   * Deliberately OUTSIDE the per-document engine and NOT cleared on a
   * switch. `saveState` describes the document on screen and is rightly
   * guarded; a failed save is a fact about the user's TEXT, and the engine
   * that discovered it is disposed moments later. Without this, typing in A
   * and switching to B before the debounce fires loses the edit in silence
   * -- and worse, returning to A re-baselines from the server's un-updated
   * content, so the editor then reports the lost text as saved.
   *
   * The PATH is part of the key because the engine's own `failed` set is
   * keyed that way and the two must not disagree. Clearing by document id
   * alone was true in the single-file era and is false now: a
   * `chapters/results.tex` that keeps 413-ing (the banner is its only loud
   * surface) had its banner wiped the moment an unrelated `main.tex` saved
   * successfully, while it was still unsaved.
   */
  const [saveFailures, setSaveFailures] = useState<
    { id: string; path: string; name: string }[]
  >([]);

  // Mirrors `selectedId` in the RENDER BODY, not from an effect (an effect
  // lands one render late). Every `await` below is followed by
  // `if (selectedIdRef.current !== docId) return;` before any state write --
  // a slow response for document A must never paint over document B. This is
  // the single guard this hook needs; the old single-file workspace also
  // kept a second `bufferDocId` ref because its buffer was loaded by a
  // separate async effect from its `selectedId` state. Here the buffer,
  // tree and metadata all load together in the one selection effect below,
  // so there is only one "has the user moved on" question to ask.
  const selectedIdRef = useRef(selectedId);
  selectedIdRef.current = selectedId;

  // Mirrors `mainPath`, so `removeFile`/`uploadBinary` can refuse a write to
  // the main file SYNCHRONOUSLY, before any network round trip -- the
  // backend would answer 409 for the same reason, but naming it locally
  // means the user sees the refusal immediately and the tab is never closed
  // on a delete that was never going to happen.
  const mainPathRef = useRef(mainPath);
  mainPathRef.current = mainPath;

  // Mirrors `openPaths`, read inside callbacks (`openFile`'s
  // already-open check, `closeFile`'s next-active-tab pick) that would
  // otherwise need `openPaths` in their dependency array and be rebuilt on
  // every tab open/close.
  const openPathsRef = useRef(openPaths);
  openPathsRef.current = openPaths;

  // Mirrors `engine`, read by `setEngine`'s rollback on a rejected PATCH.
  const engineValueRef = useRef(engine);
  engineValueRef.current = engine;

  // Mirrors `files`, so `openFile` can refuse to fetch a binary path as
  // text without needing `files` in its dependency array.
  const filesRef = useRef(files);
  filesRef.current = files;

  // Mirrors `documents`, read once when the selection effect builds a new
  // engine, to label a save-failure record with the document's NAME rather
  // than its id -- see `saveFailures`. Read through a ref rather than
  // listed as an effect dependency: the engine is rebuilt on a document
  // SWITCH, not on every rename, and a rename mid-session updating the ref
  // rather than tearing down the in-flight engine is exactly what we want.
  const documentsRef = useRef(documents);
  documentsRef.current = documents;

  // The one `SaveEngine` instance for whichever document is currently
  // selected. Built fresh in the selection effect below on every switch --
  // see that effect's comment for why "one instance in a ref" and "rebuilt
  // per document" are not in tension.
  const engineRef = useRef<SaveEngine | null>(null);

  // The in-flight `PATCH .../engine` for whichever document requested it, so
  // `awaitEnginePatch` can hand a racing `compile()` a promise to wait on --
  // see that method and `setEngine`'s own comment for the failure this
  // closes. Only ever cleared by the send whose OWN promise is still the one
  // on record (the same "only clear what's still mine" rule `inFlightSave`
  // follows in every autosave engine in this codebase), so a later patch
  // replacing this ref is never stomped by an earlier one settling after it.
  const enginePatchRef = useRef<{ docId: string; promise: Promise<void> } | null>(null);

  /**
   * Applies a `LatexMutation` response (every file write/delete/rename
   * returns one) to `revision`, `usedBytes`, and the cached `document`'s own
   * `revision` field so the two never drift apart. Guarded on
   * `selectedIdRef`: a mutation's response landing after the user has
   * switched documents must not stomp the new document's numbers. Nothing
   * else in this hook increments `revision` itself -- every call site takes
   * the number the server actually returned.
   */
  const applyMutation = useCallback((m: LatexMutation, docId: string) => {
    if (selectedIdRef.current !== docId) return;
    setRevision(m.revision);
    setUsedBytes(m.used_bytes);
    setDocumentState((prev) => (prev && prev.id === docId ? { ...prev, revision: m.revision } : prev));
  }, []);

  /** Re-lists the tree after a mutation that changed which files exist. */
  const refreshTree = useCallback(async (docId: string) => {
    const tree = await listFiles(projectId, docId);
    if (selectedIdRef.current !== docId) return;
    setFiles(tree.files);
    setUsedBytes(tree.used_bytes);
    setMaxBytes(tree.max_bytes);
  }, [projectId]);

  // ---------------------------------------------------------------------
  // Document list
  // ---------------------------------------------------------------------

  useEffect(() => {
    let cancelled = false;
    setError(null);
    listDocuments(projectId)
      .then((docs) => {
        if (cancelled) return;
        setDocuments(docs);
        // Only when nothing else owns the selection. With a `documentId`
        // the URL is the authority, and defaulting to `docs[0]` here
        // would briefly open a document the user did not navigate to.
        if (documentIdRef.current === undefined) {
          setSelectedId((current) => current ?? docs[0]?.id ?? null);
        }
      })
      .catch(() => {
        // Without this, a failed list left `documents` at its initial `[]`,
        // indistinguishable from a genuinely empty, working project.
        if (cancelled) return;
        setError("Could not load your documents. Please try again.");
      })
      .finally(() => {
        if (!cancelled) setDocumentsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  // The route's document, mirrored into the selection the rest of this hook
  // already keys off. Doing it this way rather than replacing `selectedId`
  // wholesale keeps every `selectedIdRef.current !== docId` guard below
  // working unchanged -- those guards are what stop a late response from
  // one document writing into another.
  useEffect(() => {
    if (documentId !== undefined) setSelectedId(documentId);
  }, [documentId]);

  // ---------------------------------------------------------------------
  // Selected document: metadata, tree, and the per-document SaveEngine
  // ---------------------------------------------------------------------

  useEffect(() => {
    const docId = selectedId;
    // The name to label a `saveFailures` entry with if this document's
    // autosave fails. Read from `documentsRef` once, here, rather than a
    // second `getDocument` -- the list is already loaded by the time a
    // selection can happen (either the initial autoselect or an explicit
    // `select()` from a UI that is itself rendering off this same list).
    // Falls back to the id itself on the rare miss (e.g. this document was
    // just created and `documents` hasn't caught up yet) -- a banner naming
    // an id is worse than one naming a title, but far better than none.
    const docName = docId ? (documentsRef.current.find((d) => d.id === docId)?.name ?? docId) : "";

    // A fresh engine for whichever document this effect run is about to
    // load -- buffers, baselines and pending edits are per-document and
    // must not survive a switch (rule: "switching documents must flush,
    // never just clear").
    const saveEngine = new SaveEngine({
      delayMs: AUTOSAVE_MS,
      send: async (path, text) => {
        // The document THIS engine was built for -- never resolved from
        // `selectedIdRef`. This callback is invoked from the effect CLEANUP
        // on a document switch, and React updates refs in the RENDER BODY
        // that precedes the cleanup, so by then the ref already names the
        // NEXT document. Reading it here wrote this document's text into a
        // different document's file and lost the edit with no error shown.
        // The ref decides whether to APPLY a result to what is on screen;
        // it never decides where to WRITE. `docId` is null only in the
        // branch where this engine is never given anything to save (no
        // document selected) -- the check is for the type checker, not a
        // runtime case that matters.
        if (docId === null) return;
        // The ONLY caller in this file that passes "replace". Autosave knows
        // the file exists and means to replace it; every other writer is
        // creating and keeps the server's "fail" default, which is what
        // makes a duplicate name a 409 the user gets asked about instead of
        // a silent overwrite. Do not "simplify" this to the default, and do
        // not give the default to any other caller.
        const m = await writeTextFile(projectId, docId, path, text, "replace");
        // A later send of THIS PATH that succeeds is proof this file's text
        // is safe -- and it is proof about nothing else. Cleared
        // UNCONDITIONALLY of what is on screen, same as recording one below:
        // the recovery is a fact about the data, not about whether this
        // document happens to be selected right now.
        setSaveFailures((prev) =>
          prev.filter((f) => !(f.id === docId && f.path === path))
        );
        // `SaveEngine` catches this rejection itself and turns it into the
        // "error" save state and a lasting dirty flag -- no try/catch here,
        // that would make a failed save look clean.
        if (selectedIdRef.current !== docId) return; // no longer on screen
        applyMutation(m, docId);
      },
      onStateChange: (state, path) => {
        if (docId !== null && state === "error") {
          // Recorded UNCONDITIONALLY, before the display guard below. A
          // failed save is a fact about the user's TEXT, not about which
          // document is on screen -- and the engine that discovered this is
          // disposed moments after a switch. Without this, typing in A and
          // switching to B before the debounce fires (or while the send is
          // in flight) loses A's edit in total silence: no badge, and
          // returning to A later re-baselines from the server's un-updated
          // content, so the editor then reports the lost text as saved.
          setSaveFailures((prev) =>
            prev.some((f) => f.id === docId && f.path === path)
              ? prev
              : [...prev, { id: docId, path, name: docName }]
          );
        }
        // The badge IS per-document -- it describes whatever is on SCREEN
        // right now -- so it stays guarded. `saveFailures` above is the
        // half of this signal that must not be.
        if (selectedIdRef.current !== docId) return;
        setSaveState(state);
      },
    });
    engineRef.current = saveEngine;

    setSaveState("idle");
    setError(null);
    // Per-document UI state resets THE INSTANT the selection changes, not
    // once the new document's load resolves -- otherwise document A's tabs
    // and buffers keep rendering under document B's name for however long
    // the network takes.
    setOpenPaths([]);
    setActivePath(null);
    setBuffers({});

    if (!docId) {
      setDocumentState(null);
      setRevision(null);
      setMainPathState(null);
      setEngineState("pdflatex");
      setFiles([]);
      setUsedBytes(0);
      setMaxBytes(0);
      setDocLoading(false);
      return () => {
        void saveEngine.flushAll().finally(() => saveEngine.dispose());
      };
    }

    let cancelled = false;
    setDocLoading(true);
    Promise.all([getDocument(projectId, docId), listFiles(projectId, docId)])
      .then(([doc, tree]) => {
        if (cancelled || selectedIdRef.current !== docId) return;
        setDocumentState(doc);
        setRevision(doc.revision);
        setMainPathState(doc.main_path);
        setEngineState(doc.engine);
        setFiles(tree.files);
        setUsedBytes(tree.used_bytes);
        setMaxBytes(tree.max_bytes);
      })
      .catch(() => {
        // A collaborator deletes this document, the backend 5xxs, a reload
        // races the request -- whatever the cause, this tab must not go on
        // presenting a document it does not have. Clearing the selection
        // reuses the `!docId` branch above (already correct) rather than
        // inventing a second place that has to keep `document`/`files`/etc.
        // consistent with "nothing selected".
        if (cancelled || selectedIdRef.current !== docId) return;
        setError("Could not load that document. Please try again.");
        setSelectedId(null);
      })
      .finally(() => {
        if (cancelled || selectedIdRef.current !== docId) return;
        setDocLoading(false);
      });

    return () => {
      cancelled = true;
      // Flush THEN dispose, in that order: `flushAll` launches (and awaits)
      // every pending/in-flight send while the engine can still accept one,
      // and only once that settles does `dispose` stop it from accepting
      // any more. Clearing the pending edits instead of flushing them would
      // silently drop a file's worth of typing with no error shown -- the
      // same rule the single-file predecessor's `flush`-on-switch enforced.
      void saveEngine.flushAll().finally(() => saveEngine.dispose());
    };
  }, [projectId, selectedId, applyMutation]);

  // On unmount specifically (as opposed to every selection change), the
  // effect above already tears down whichever engine was last built via its
  // own cleanup -- nothing further is needed here. This effect exists only
  // to satisfy the brief's "on unmount: engine.dispose()" rule with an
  // explicit statement of that fact, in the established cancelled-flag
  // pattern used everywhere else in this hook.
  useEffect(() => {
    return () => {
      engineRef.current?.dispose();
    };
  }, []);

  // ---------------------------------------------------------------------
  // Tabs and buffers
  // ---------------------------------------------------------------------

  const select = useCallback((id: string | null) => {
    setSelectedId(id);
  }, []);

  const openFile = useCallback(
    async (path: string) => {
      const docId = selectedIdRef.current;
      if (!docId) return;
      if (openPathsRef.current.includes(path)) {
        // Already open -- just bring the existing tab forward, no re-fetch.
        setActivePath(path);
        return;
      }
      // A binary path is never fetched as text here -- `binary-preview.tsx`
      // fetches it on demand. Guards on the server's own `is_binary` flag
      // (not an extension heuristic): a stale click reaching here must not
      // corrupt a blob into a JS string. The TAB still opens, though --
      // only the text fetch and buffer are skipped, so the shell's
      // `isTexPath` check can route the pane to `BinaryPreview` once
      // `activePath` names it. `BinaryPreview` never reads `buffers`, so an
      // absent entry for this path is not a missing state, it is correct.
      const meta = filesRef.current.find((f) => f.path === path);
      if (meta?.is_binary) {
        setOpenPaths((prev) => (prev.includes(path) ? prev : [...prev, path]));
        setActivePath(path);
        return;
      }
      // A pending or in-flight autosave for this path must LAND before the
      // re-fetch below, or the fetch reads the pre-edit content and then
      // re-baselines against it.
      //
      // The sequence this closes: `closeFile` drops the buffer (so a reopen
      // always re-fetches) and deliberately leaves the `SaveEngine` alone (so
      // a pending autosave still fires on its own timer). Close a tab and
      // reopen it inside the 800ms debounce window and the GET returned the
      // OLD text, which then became both the buffer AND the new baseline;
      // the pending PUT wrote the good text to the server moments later.
      // The editor showed stale text, `isDirty()` said clean, and the next
      // keystroke overwrote the server's good content with it.
      //
      // `flushPath` is safe to call for a path with nothing pending -- it
      // awaits any in-flight send and returns -- but the `dirtyPaths` test
      // keeps the common case (opening a file nobody has touched) free of an
      // extra await, and says out loud which paths this is for.
      const saveEngine = engineRef.current;
      if (saveEngine?.dirtyPaths().includes(path)) {
        await saveEngine.flushPath(path);
        if (selectedIdRef.current !== docId) return;
      }
      let text: string;
      try {
        text = await readTextFile(projectId, docId, path);
      } catch (err) {
        if (selectedIdRef.current !== docId) return;
        setError(errorText(err));
        return;
      }
      if (selectedIdRef.current !== docId) return;
      setBuffers((prev) => ({ ...prev, [path]: text }));
      engineRef.current?.setBaseline(path, text);
      setOpenPaths((prev) => (prev.includes(path) ? prev : [...prev, path]));
      setActivePath(path);
    },
    [projectId]
  );

  const closeFile = useCallback((path: string) => {
    setOpenPaths((prev) => prev.filter((p) => p !== path));
    // The buffer entry is dropped too, not just the tab: reopening the same
    // path must always re-fetch from the server rather than redisplay a
    // possibly-stale copy. This does NOT touch `SaveEngine` -- a pending
    // autosave for `path` keeps its own timer and still fires on schedule
    // whether or not the tab showing it is open, so closing a tab can never
    // silently drop an edit.
    setBuffers((prev) => {
      if (!(path in prev)) return prev;
      const next = { ...prev };
      delete next[path];
      return next;
    });
    setActivePath((prev) => {
      if (prev !== path) return prev;
      const remaining = openPathsRef.current.filter((p) => p !== path);
      return remaining[remaining.length - 1] ?? null;
    });
  }, []);

  const editBuffer = useCallback(
    (path: string, text: string) => {
      if (!canEdit) return;
      setBuffers((prev) => ({ ...prev, [path]: text }));
      engineRef.current?.schedule(path, text);
    },
    [canEdit]
  );

  // ---------------------------------------------------------------------
  // File mutations
  // ---------------------------------------------------------------------

  const createFile = useCallback(
    async (path: string) => {
      const docId = selectedIdRef.current;
      if (!docId || !canEdit) return;
      setError(null);
      try {
        // "fail" (the default) on purpose: this is a CREATE, and a path that
        // already exists must come back as a 409 the caller can offer a
        // `(n)` name for -- never as a silent overwrite.
        const m = await writeTextFile(projectId, docId, path, "");
        applyMutation(m, docId);
        await refreshTree(docId);
      } catch (err) {
        if (selectedIdRef.current !== docId) return;
        // Rethrown UNCHANGED so the caller can open the conflict dialog.
        // Folding it into `error` text here would hide the suggestion the
        // dialog is built from -- the same reason the import flow carries
        // its payload as a typed error rather than a sentence.
        if (err instanceof PathCollisionError) throw err;
        setError(errorText(err));
      }
    },
    [projectId, canEdit, applyMutation, refreshTree]
  );

  const removeFile = useCallback(
    async (path: string) => {
      const docId = selectedIdRef.current;
      if (!docId || !canEdit) return;
      // Refused locally, matching the backend's own 409: deleting the main
      // file leaves the document with no source and no compile can recover
      // it. Refusing here means the user gets the message without a round
      // trip, and the tab below is never closed on a delete that will not
      // happen.
      if (path === mainPathRef.current) {
        setError("The main file can't be deleted. Set a different main file first.");
        return;
      }
      setError(null);
      // Forgotten BEFORE the DELETE: a pending PUT against a path that is
      // about to stop existing would otherwise 404 the moment it fires,
      // surfacing as a stray "Could not save" under whatever file is open
      // by then.
      engineRef.current?.forget(path);
      // The banner is the OTHER half of the same fact, and it has to move
      // with the engine's `failed` flag or it strands. `forget` clears the
      // engine's flag for this path; a path-keyed `saveFailures` entry left
      // behind can never be cleared again -- nothing will ever save this
      // path successfully, because it is about to stop existing -- so the
      // banner would sit there for the rest of the session. Before the
      // failure record was keyed by path, ANY successful save cleared it,
      // so leaving this out is strictly worse than the behaviour it
      // replaced. Done unconditionally of what is on screen, exactly like
      // the recording in `onStateChange`.
      setSaveFailures((prev) => prev.filter((f) => !(f.id === docId && f.path === path)));
      try {
        const m = await deleteFile(projectId, docId, path);
        applyMutation(m, docId);
        if (selectedIdRef.current !== docId) return;
        setOpenPaths((prev) => prev.filter((p) => p !== path));
        setBuffers((prev) => {
          if (!(path in prev)) return prev;
          const next = { ...prev };
          delete next[path];
          return next;
        });
        setActivePath((prev) => {
          if (prev !== path) return prev;
          const remaining = openPathsRef.current.filter((p) => p !== path);
          return remaining[remaining.length - 1] ?? null;
        });
        await refreshTree(docId);
      } catch (err) {
        if (selectedIdRef.current !== docId) return;
        setError(errorText(err));
      }
    },
    [projectId, canEdit, applyMutation, refreshTree]
  );

  const moveFile = useCallback(
    async (from: string, to: string) => {
      const docId = selectedIdRef.current;
      if (!docId || !canEdit) return;
      setError(null);
      // CONFIRM, then rename the engine -- never the other way round.
      //
      // Renaming the engine first moved the pending text under the
      // DESTINATION path and armed a timer for it. When the backend then
      // refused with 409 `PathCollision` -- which exists precisely to
      // protect the file already at the destination -- that timer fired
      // anyway and PUT one file's text into the protected file, and since
      // `refreshTree` is not called on the failure path the tree did not
      // even show it. Undoing the engine rename in the catch would close
      // the same hole, but only after the timer has had a whole round trip
      // to fire; not moving it until the server has agreed leaves no window
      // at all.
      //
      // The pending edit for `from` is flushed FIRST, while `from` is still
      // the name the server knows. Without that, a debounce timer firing
      // during the round trip races the server-side rename and 404s against
      // a path that has just stopped existing -- the same stray "Could not
      // save" `removeFile` forgets a path to avoid. After the flush the
      // engine carries only a baseline (and possibly a `failed` flag) for
      // `from`, which `rename` moves across without a timer.
      const saveEngine = engineRef.current;
      if (saveEngine?.dirtyPaths().includes(from)) {
        await saveEngine.flushPath(from);
        if (selectedIdRef.current !== docId) return;
      }
      try {
        const m = await renameFile(projectId, docId, from, to);
        // The engine captured ABOVE, not `engineRef.current`: by the time
        // this resolves the ref may already name the next document's engine,
        // and renaming a path inside that one is a different file entirely.
        saveEngine?.rename(from, to);
        // Mirrors `SaveEngine.rename`'s own migration of its `failed` flag.
        // These two stores are two views of ONE truth -- "this file holds
        // text the server does not" -- and they drifted the moment only one
        // of them learned about paths. Without this the engine tracks the
        // NEW path while the banner still names the OLD one, so a later
        // successful save clears the engine's flag and can never clear the
        // banner: it is permanent for the session.
        setSaveFailures((prev) =>
          prev.map((f) => (f.id === docId && f.path === from ? { ...f, path: to } : f))
        );
        applyMutation(m, docId);
        if (selectedIdRef.current !== docId) return;
        setOpenPaths((prev) => prev.map((p) => (p === from ? to : p)));
        setBuffers((prev) => {
          if (!(from in prev)) return prev;
          const next = { ...prev };
          next[to] = next[from];
          delete next[from];
          return next;
        });
        setActivePath((prev) => (prev === from ? to : prev));
        await refreshTree(docId);
      } catch (err) {
        if (selectedIdRef.current !== docId) return;
        // Rethrown UNCHANGED -- see `createFile`. Note this is reached with
        // the engine NOT yet renamed (the rename above runs only on the
        // success path), so a caller retrying at a different destination
        // starts from exactly the state it would have had.
        if (err instanceof PathCollisionError) throw err;
        setError(errorText(err));
      }
    },
    [projectId, canEdit, applyMutation, refreshTree]
  );

  const uploadBinary = useCallback(
    async (path: string, data: Blob) => {
      const docId = selectedIdRef.current;
      if (!docId || !canEdit) return;
      // Same refusal as `removeFile`, for the other way a document can lose
      // its source: overwriting the main file with binary content.
      if (path === mainPathRef.current) {
        setError("The main file can't be replaced with binary content.");
        return;
      }
      setError(null);
      try {
        const m = await writeBinaryFile(projectId, docId, path, data);
        applyMutation(m, docId);
        await refreshTree(docId);
      } catch (err) {
        if (selectedIdRef.current !== docId) return;
        // Rethrown UNCHANGED -- see `createFile`.
        if (err instanceof PathCollisionError) throw err;
        setError(errorText(err));
      }
    },
    [projectId, canEdit, applyMutation, refreshTree]
  );

  // ---------------------------------------------------------------------
  // Document-level settings
  // ---------------------------------------------------------------------

  const setEngine = useCallback(
    (next: LatexEngine): Promise<void> => {
      const docId = selectedIdRef.current;
      if (!docId || !canEdit) return Promise.resolve();
      const previous = engineValueRef.current;
      setEngineState(next);
      setError(null);
      // Stored in `enginePatchRef` BEFORE the network call settles, not
      // just awaited inline: `awaitEnginePatch` needs something to hand a
      // racing `compile()` while this is still on the wire, or picking
      // xelatex and hitting Compile inside this PATCH's round trip builds
      // with whatever engine the server still has.
      const promise = patchDocument(projectId, docId, { engine: next })
        .then((doc) => {
          if (selectedIdRef.current !== docId) return;
          setDocumentState(doc);
          setRevision(doc.revision);
          setEngineState(doc.engine);
          setMainPathState(doc.main_path);
          setDocuments((prev) => prev.map((d) => (d.id === doc.id ? doc : d)));
        })
        .catch((err) => {
          // Guarded on `selectedIdRef`: by the time this settles the user
          // may have switched to a different document entirely, whose OWN
          // engine this must not stomp.
          if (selectedIdRef.current !== docId) return;
          setEngineState(previous);
          setError(errorText(err));
        })
        .finally(() => {
          // Only clear if this call's own patch is still the one on
          // record -- see `inFlightSave`'s identical guard in the
          // single-file predecessor this hook replaces.
          if (enginePatchRef.current?.promise === promise) enginePatchRef.current = null;
        });
      enginePatchRef.current = { docId, promise };
      return promise;
    },
    [projectId, canEdit]
  );

  const awaitEnginePatch = useCallback(async (docId: string) => {
    if (enginePatchRef.current?.docId === docId) {
      await enginePatchRef.current.promise;
    }
  }, []);

  const setMainPath = useCallback(
    async (path: string) => {
      const docId = selectedIdRef.current;
      if (!docId || !canEdit) return;
      const previous = mainPathRef.current;
      setMainPathState(path);
      setError(null);
      try {
        const doc = await patchDocument(projectId, docId, { main_path: path });
        if (selectedIdRef.current !== docId) return;
        setDocumentState(doc);
        setRevision(doc.revision);
        setMainPathState(doc.main_path);
        setDocuments((prev) => prev.map((d) => (d.id === doc.id ? doc : d)));
      } catch (err) {
        if (selectedIdRef.current !== docId) return;
        setMainPathState(previous);
        setError(errorText(err));
      }
    },
    [projectId, canEdit]
  );

  // ---------------------------------------------------------------------
  // Document list mutations
  // ---------------------------------------------------------------------

  const createDoc = useCallback(
    async (name: string, source?: string) => {
      setError(null);
      try {
        // `source` seeds the new document's main file -- the backend
        // itself defaults to an EMPTY file, so a starter template is the
        // caller's choice, not this hook's. Passed through verbatim (never
        // defaulted here) so a caller that wants a blank document still
        // gets one.
        const doc = await createDocument(projectId, { name, source });
        setDocuments((prev) => [...prev, doc]);
        setSelectedId(doc.id);
      } catch (err) {
        // A duplicate NAME (409 `name_collision`) does NOT get the shared
        // conflict dialog here, unlike the projects list page: this hook has
        // no dialog host, and nothing in the workspace calls `createDoc` --
        // the only surface that creates a document is the list page, which
        // owns its own `ConflictDialog`. Rethrowing instead would hand a
        // typed error to a caller that does not exist. What this path must
        // still do is SAY what happened, and `errorText` now names the taken
        // name and the server's suggestion rather than the generic line.
        setError(errorText(err));
      }
    },
    [projectId]
  );

  const removeDoc = useCallback(
    async (id: string) => {
      setError(null);
      // If the document being removed is the one currently open, its
      // pending edits must not survive to be flushed against a document
      // that is about to be gone: the selection effect's cleanup will flush
      // on the next render (deselecting always flushes), and without this a
      // pending PUT would fire against a deleted document and 404, showing
      // a "Could not save" banner under whatever comes up next for no
      // reason a user could connect to anything they did.
      if (id === selectedIdRef.current) {
        const engine = engineRef.current;
        if (engine) {
          for (const path of openPathsRef.current) engine.forget(path);
        }
      }
      try {
        await deleteDocument(projectId, id);
      } catch (err) {
        setError(errorText(err));
        return;
      }
      setDocuments((prev) => prev.filter((d) => d.id !== id));
      // A save-failure entry must not outlive the document it names -- the
      // banner would go on naming a document that no longer exists, with a
      // dismiss button as its only way out.
      setSaveFailures((prev) => prev.filter((f) => f.id !== id));
      setSelectedId((current) => (current === id ? null : current));
    },
    [projectId]
  );

  // The import itself is NOT performed here. It is a two-step plan/commit
  // conversation with the user (collisions, a duplicate document name, an
  // undecidable main file), and it is offered from the projects LIST page
  // too, which has no hook at all -- so the flow lives in
  // `import-dropzone.tsx`, the one component both surfaces share. What is
  // left here is only what a committed import means for state this hook
  // owns.

  const refreshFiles = useCallback(async (revision?: number) => {
    const docId = selectedIdRef.current;
    if (!docId) return;
    try {
      await refreshTree(docId);
      // Taken from the server's own response rather than re-fetched with a
      // second `getDocument`: this hook's rule is that nothing increments
      // `revision` itself, every call site uses the number the server
      // actually returned (see `applyMutation`), and the commit response
      // already carries it. `used_bytes` is not folded in here because
      // `refreshTree` just took it from the freshly listed tree.
      if (revision !== undefined && selectedIdRef.current === docId) {
        setRevision(revision);
        setDocumentState((prev) =>
          prev && prev.id === docId ? { ...prev, revision } : prev
        );
      }
    } catch (err) {
      if (selectedIdRef.current !== docId) return;
      setError(errorText(err));
    }
  }, [refreshTree]);

  const adoptDocument = useCallback(
    async (id: string) => {
      setError(null);
      try {
        const doc = await getDocument(projectId, id);
        setDocuments((prev) => (prev.some((d) => d.id === doc.id) ? prev : [...prev, doc]));
        setSelectedId(doc.id);
      } catch (err) {
        // The import itself succeeded server-side; only the follow-up fetch
        // of the full document failed. The new document exists but is not
        // yet reflected in `documents` -- the next full list load picks it
        // up. Reported as an ordinary error rather than silently dropped.
        setError(errorText(err));
      }
    },
    [projectId]
  );

  // ---------------------------------------------------------------------
  // Save state surface
  // ---------------------------------------------------------------------

  const isDirty = useCallback(() => engineRef.current?.isDirty() ?? false, []);

  const flushAll = useCallback(async () => {
    await engineRef.current?.flushAll();
  }, []);

  const dismissSaveFailure = useCallback((id: string, path: string) => {
    setSaveFailures((prev) => prev.filter((f) => !(f.id === id && f.path === path)));
  }, []);

  const reportError = useCallback((err: unknown) => {
    setError(errorText(err));
  }, []);

  return {
    documents,
    selectedId,
    select,
    document: documentState,
    revision,
    mainPath,
    engine,
    files,
    usedBytes,
    maxBytes,
    openPaths,
    activePath,
    buffers,
    saveState,
    // Recomputed on every render -- a cheap set union over the engine's own
    // maps, not worth memoizing.
    dirtyPaths: engineRef.current?.dirtyPaths() ?? [],
    saveFailures,
    dismissSaveFailure,
    reportError,
    loading: documentsLoading || docLoading,
    error,
    isDirty,
    flushAll,
    openFile,
    closeFile,
    editBuffer,
    createFile,
    removeFile,
    moveFile,
    uploadBinary,
    setEngine,
    awaitEnginePatch,
    setMainPath,
    createDoc,
    removeDoc,
    refreshFiles,
    adoptDocument,
  };
}
