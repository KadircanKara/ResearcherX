"use client";

import { useEffect, useRef } from "react";
import { Compartment, EditorState, type Extension } from "@codemirror/state";
import { EditorView, keymap, lineNumbers, highlightActiveLine } from "@codemirror/view";
import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import { bracketMatching, syntaxHighlighting } from "@codemirror/language";
import { closeBrackets } from "@codemirror/autocomplete";
import { latexHighlightStyle, texLanguage } from "@/lib/latex-syntax";

/**
 * The editor's own chrome -- gutter, active line, caret, selection.
 *
 * CodeMirror's base theme hardcodes a light gutter and a light selection
 * whatever the page is doing, so without this the editor stayed light-themed
 * inside a dark app. Every colour comes from the app's semantic tokens,
 * which is why they are wrapped in `oklch(...)` here: those tokens hold bare
 * channels for Tailwind's `oklch(var(--token) / <alpha>)` form. Defined once
 * at module scope, not per render -- CodeMirror compiles a theme into a
 * stylesheet, and a fresh object each render would mount a new one.
 */
const editorTheme = EditorView.theme({
  "&": { height: "100%", backgroundColor: "transparent", color: "oklch(var(--foreground))" },
  "&.cm-focused": { outline: "none" },
  ".cm-scroller": { fontFamily: "var(--font-mono)", lineHeight: "1.6" },
  ".cm-content": { caretColor: "oklch(var(--foreground))" },
  ".cm-gutters": {
    backgroundColor: "transparent",
    color: "oklch(var(--muted-foreground))",
    border: "none",
  },
  ".cm-activeLine": { backgroundColor: "oklch(var(--muted) / 0.45)" },
  ".cm-activeLineGutter": {
    backgroundColor: "transparent",
    color: "oklch(var(--foreground))",
  },
  ".cm-selectionBackground, &.cm-focused .cm-selectionBackground": {
    backgroundColor: "oklch(var(--accent))",
  },
});

interface EditorPaneProps {
  /** Which buffer is showing. Null when no file is open. */
  path: string | null;
  /**
   * Every path currently open in a tab.
   *
   * Needed because `statesRef` is keyed on the path STRING, and a path can
   * be reused: delete `a.tex`, create a new `a.tex`, open it, and a map
   * that was never pruned hands back the dead file's EditorState -- its
   * text is corrected by the value effect, but its UNDO HISTORY is not, so
   * Ctrl+Z resurrects content the user deleted. This component only ever
   * sees the ACTIVE path, so it cannot tell a dead key from a closed tab
   * without being told.
   */
  openPaths: string[];
  value: string;
  onChange: (next: string) => void;
  onLineDoubleClick: (line: number) => void;
  /** The nonce makes a repeat jump to the same line still scroll. */
  gotoLine: { line: number; nonce: number } | null;
  readOnly: boolean;
}

export function EditorPane({
  path,
  openPaths,
  value,
  onChange,
  onLineDoubleClick,
  gotoLine,
  readOnly,
}: EditorPaneProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  // Callbacks live in a ref so the editor is constructed exactly once. Listing
  // them as effect dependencies would tear down and rebuild the whole view on
  // every parent render, losing the cursor and the undo history each time.
  const handlers = useRef({ onChange, onLineDoubleClick });
  handlers.current = { onChange, onLineDoubleClick };
  // readOnly is reconfigured through a Compartment rather than being baked
  // into the initial EditorState and left there. The same reason the
  // callbacks live in a ref applies: rebuilding the view to pick up a new
  // value would lose the cursor and the undo history. There is exactly ONE
  // Compartment instance for the whole component's lifetime -- it is
  // embedded into every per-path EditorState created below, so
  // reconfiguring it (done both by the readOnly effect and by the path-switch
  // effect) always targets whichever state the view currently holds.
  const readOnlyCompartment = useRef(new Compartment());

  // One EditorState per open file, keyed by path. The view itself is built
  // exactly once (below) and its live `.state` is swapped out from this map
  // on every path switch -- this is the whole reason NOT to remount the
  // editor per file with something like `key={path}`. Remounting would
  // throw away that file's undo history and cursor every time the user
  // clicked another tab, which is the same failure the callbacks-in-a-ref
  // and the readOnly Compartment above exist to prevent, one level up.
  const statesRef = useRef<Map<string, EditorState>>(new Map());
  // The path the view is CURRENTLY displaying, so the path-switch effect
  // knows which outgoing buffer's live state to stash before loading the
  // incoming one. Not the same thing as the `path` prop during a render
  // where they're about to diverge -- this ref only updates once the switch
  // has actually happened.
  const activePathRef = useRef<string | null>(null);

  // Shared by both the construct-once effect (for the very first buffer) and
  // the path-switch effect (for every buffer opened after that) so the two
  // can never drift into building EditorStates with different extension
  // sets. Reads `handlers.current` and `readOnlyCompartment.current` inside
  // its closures, not their captured values, so a state built long after
  // mount still calls whatever the latest callbacks are.
  function buildExtensions(initialReadOnly: boolean): Extension[] {
    return [
      lineNumbers(),
      highlightActiveLine(),
      history(),
      bracketMatching(),
      closeBrackets(),
      keymap.of([...defaultKeymap, ...historyKeymap]),
      texLanguage,
      // Without this the editor has NO syntax colouring at all: a stream
      // language only assigns highlight tags, and `basicSetup` -- which is
      // where a default `syntaxHighlighting` would otherwise come from --
      // is deliberately not used here.
      syntaxHighlighting(latexHighlightStyle),
      editorTheme,
      EditorView.lineWrapping,
      EditorView.updateListener.of((update) => {
        if (update.docChanged) handlers.current.onChange(update.state.doc.toString());
      }),
      EditorView.domEventHandlers({
        dblclick(event, view) {
          const pos = view.posAtCoords({ x: event.clientX, y: event.clientY });
          if (pos === null) return false;
          handlers.current.onLineDoubleClick(view.state.doc.lineAt(pos).number);
          // false: let CodeMirror keep its own word-selection behaviour.
          // Sync is an addition to the double-click, not a replacement.
          return false;
        },
      }),
      readOnlyCompartment.current.of(EditorState.readOnly.of(initialReadOnly)),
    ];
  }

  useEffect(() => {
    if (!hostRef.current || viewRef.current) return;

    const view = new EditorView({
      parent: hostRef.current,
      state: EditorState.create({ doc: value, extensions: buildExtensions(readOnly) }),
    });
    viewRef.current = view;
    activePathRef.current = path;
    if (path !== null) statesRef.current.set(path, view.state);

    return () => {
      view.destroy();
      viewRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Switching the active file. Declared BEFORE the external-value-sync
  // effect below so it always runs first within the same commit: when a
  // parent changes `path` and `value` together (opening a different tab),
  // the sync effect must see the ALREADY-SWAPPED state, or it would compare
  // the outgoing buffer's document against the incoming buffer's `value` and
  // insert one file's text into another's undo history.
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    const prevPath = activePathRef.current;
    if (prevPath === path) return; // already showing this buffer (e.g. first mount)

    if (prevPath !== null) statesRef.current.set(prevPath, view.state);
    activePathRef.current = path;
    if (path === null) return; // nothing open; leave the view showing the last buffer

    const nextState =
      statesRef.current.get(path) ?? EditorState.create({ doc: value, extensions: buildExtensions(readOnly) });
    view.setState(nextState);
    // A cached state's readOnly compartment reflects whatever it was last
    // reconfigured to (or its creation-time default for a brand-new one),
    // which can be stale by the time this buffer is revisited -- the
    // readOnly effect further down only fires on ITS OWN dependency
    // changing, not on a path switch, so this dispatch is what keeps a
    // locked-while-compiling editor locked after switching tabs mid-compile.
    view.dispatch({
      effects: readOnlyCompartment.current.reconfigure(EditorState.readOnly.of(readOnly)),
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path]);

  // Drop cached buffers for paths that are no longer open. Closing a tab
  // therefore discards that file's undo history -- deliberate: the
  // alternative is a map keyed on a string that can be reused by a
  // different file, and resurrecting a deleted file's edits is far worse
  // than losing undo across a close. A rename is covered by the same rule,
  // since the old path leaves `openPaths` and the new one arrives with no
  // cached state.
  //
  // Declared AFTER the path-switch effect above, which matters when a tab
  // switch and a tab close land in the SAME commit (e.g. selecting a
  // neighbour tab and closing the one just left, both driven off one state
  // update in the parent): the switch effect has already stashed the
  // outgoing buffer's live state into `statesRef` by the time this effect
  // runs, so pruning here only ever removes a state that has already been
  // safely handed off -- it can never delete the buffer a switch is still
  // in the middle of stashing.
  useEffect(() => {
    const live = new Set(openPaths);
    for (const cached of statesRef.current.keys()) {
      if (!live.has(cached)) statesRef.current.delete(cached);
    }
  }, [openPaths]);

  // Push an externally-changed document in (e.g. a fresh fetch landing for
  // the currently-open file), but never echo back what the user just typed
  // -- that would reset the cursor on every keystroke. Guarded on `path`
  // being non-null: with no buffer open there is nothing this could
  // meaningfully compare `value` against, and the path-switch effect above
  // already handles loading a newly-opened buffer's own text.
  useEffect(() => {
    const view = viewRef.current;
    if (!view || path === null) return;
    const current = view.state.doc.toString();
    if (current === value) return;
    view.dispatch({ changes: { from: 0, to: current.length, insert: value } });
  }, [value, path]);

  // Previously readOnly was only read once, inside the construct-once effect
  // above -- a parent flipping it after mount (e.g. locking the editor while
  // a compile runs) silently had no effect on actual editability. Dispatching
  // through the compartment reconfigures the live view instead.
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    view.dispatch({
      effects: readOnlyCompartment.current.reconfigure(EditorState.readOnly.of(readOnly)),
    });
  }, [readOnly]);

  useEffect(() => {
    const view = viewRef.current;
    if (!view || !gotoLine) return;
    const total = view.state.doc.lines;
    // A stale map can name a line past the end of an edited buffer; clamping
    // beats throwing out of an effect.
    const target = Math.min(Math.max(gotoLine.line, 1), total);
    const line = view.state.doc.line(target);
    view.dispatch({
      selection: { anchor: line.from, head: line.to },
      effects: EditorView.scrollIntoView(line.from, { y: "center" }),
    });
    view.focus();
  }, [gotoLine]);

  return <div ref={hostRef} className="h-full overflow-auto text-sm" />;
}
