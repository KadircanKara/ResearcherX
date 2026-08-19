"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { DocumentList } from "@/components/latex/document-list";
import { EditorPane } from "@/components/latex/editor-pane";
import { PdfViewer } from "@/components/latex/pdf-viewer";
import {
  createDocument,
  deleteDocument,
  getDocument,
  listDocuments,
  patchDocument,
  type LatexDocument,
} from "@/lib/latex";
import type { Role } from "@/lib/types";

const CAN_EDIT: Role[] = ["owner", "editor"];
const AUTOSAVE_MS = 800;

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

  const [documents, setDocuments] = useState<LatexDocument[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [source, setSource] = useState("");
  const [loading, setLoading] = useState(true);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "error">("idle");
  const [splitPercent, setSplitPercent] = useState(50);

  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // The last text this component knows the server has. Autosave compares
  // against it so a PATCH is skipped when nothing actually changed -- and so
  // selecting a document does not immediately save it back unchanged.
  const savedSource = useRef("");

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
    if (!selectedId) {
      setSource("");
      savedSource.current = "";
      return;
    }
    let cancelled = false;
    getDocument(projectId, selectedId).then((doc) => {
      if (cancelled) return;
      setSource(doc.source);
      savedSource.current = doc.source;
    });
    return () => {
      cancelled = true;
    };
  }, [projectId, selectedId]);

  // Autosave: debounced, and deliberately independent of compiling. Saving
  // preserves work; compiling costs a container run. Tying them together
  // would either lose edits or queue seconds-long runs behind every pause.
  const scheduleSave = useCallback(
    (next: string) => {
      if (!canEdit || !selectedId) return;
      if (saveTimer.current) clearTimeout(saveTimer.current);
      saveTimer.current = setTimeout(() => {
        if (next === savedSource.current) return;
        setSaveState("saving");
        patchDocument(projectId, selectedId, { source: next })
          .then(() => {
            savedSource.current = next;
            setSaveState("idle");
          })
          .catch(() => setSaveState("error"));
      }, AUTOSAVE_MS);
    },
    [canEdit, projectId, selectedId]
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

  async function handleCreate(name: string) {
    const doc = await createDocument(projectId, { name, source: STARTER });
    setDocuments((prev) => [...prev, doc]);
    setSelectedId(doc.id);
  }

  async function handleDelete(id: string) {
    await deleteDocument(projectId, id);
    setDocuments((prev) => prev.filter((d) => d.id !== id));
    setSelectedId((current) => (current === id ? null : current));
  }

  // Drag handle. Clamped so neither pane can be dragged out of existence.
  function startDrag(e: React.PointerEvent<HTMLDivElement>) {
    const host = e.currentTarget.parentElement;
    if (!host) return;
    const box = host.getBoundingClientRect();
    const move = (ev: PointerEvent) => {
      const pct = ((ev.clientX - box.left) / box.width) * 100;
      setSplitPercent(Math.min(75, Math.max(25, pct)));
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

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
                onLineDoubleClick={() => {}}
                gotoLine={null}
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
            <div className="border-b border-border px-3 py-1.5 text-xs text-muted-foreground">
              Preview
            </div>
            <div className="flex-1 overflow-hidden">
              <PdfViewer
                bytes={null}
                scale={1.25}
                highlight={null}
                scrollToPage={null}
                onPageDoubleClick={() => {}}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
