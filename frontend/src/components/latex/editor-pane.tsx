"use client";

import { useEffect, useRef } from "react";
import { Compartment, EditorState } from "@codemirror/state";
import { EditorView, keymap, lineNumbers, highlightActiveLine } from "@codemirror/view";
import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import { StreamLanguage, bracketMatching } from "@codemirror/language";
import { closeBrackets } from "@codemirror/autocomplete";
import { stex } from "@codemirror/legacy-modes/mode/stex";

interface EditorPaneProps {
  value: string;
  onChange: (next: string) => void;
  onLineDoubleClick: (line: number) => void;
  /** The nonce makes a repeat jump to the same line still scroll. */
  gotoLine: { line: number; nonce: number } | null;
  readOnly: boolean;
}

export function EditorPane({
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
  // value would lose the cursor and the undo history.
  const readOnlyCompartment = useRef(new Compartment());

  useEffect(() => {
    if (!hostRef.current || viewRef.current) return;

    const view = new EditorView({
      parent: hostRef.current,
      state: EditorState.create({
        doc: value,
        extensions: [
          lineNumbers(),
          highlightActiveLine(),
          history(),
          bracketMatching(),
          closeBrackets(),
          keymap.of([...defaultKeymap, ...historyKeymap]),
          StreamLanguage.define(stex),
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
          readOnlyCompartment.current.of(EditorState.readOnly.of(readOnly)),
        ],
      }),
    });
    viewRef.current = view;

    return () => {
      view.destroy();
      viewRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Push an externally-changed document in (switching documents), but never
  // echo back what the user just typed -- that would reset the cursor on every
  // keystroke.
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    const current = view.state.doc.toString();
    if (current === value) return;
    view.dispatch({ changes: { from: 0, to: current.length, insert: value } });
  }, [value]);

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
