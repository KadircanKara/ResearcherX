"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { FileText, Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { PaperDialog } from "@/components/paper-dialog";
import { getProject, listPapers, deletePaper } from "@/lib/projects";
import type { Paper, Role } from "@/lib/types";

const CAN_ADD: Role[] = ["owner", "editor"];

function fmtDate(iso: string) {
  return new Date(iso).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

export default function PapersPage() {
  const { id: projectId } = useParams<{ id: string }>();
  const [papers, setPapers] = useState<Paper[]>([]);
  const [myRole, setMyRole] = useState<Role | null>(null);
  const [loading, setLoading] = useState(true);
  const [deleting, setDeleting] = useState<string | null>(null);

  // `silent` skips the full-page loading skeleton. The skeleton branch below
  // doesn't render <PaperDialog>, so a non-silent reload while the Add Paper
  // dialog is open unmounts it out from under the user — e.g. PaperUploadScreen
  // calls onSaved (this function) mid-batch, and an open dialog would vanish
  // instead of staying open to show a failed row.
  //
  // `loadSeq` guards against out-of-order resolution: onSaved (batch
  // completion) and handleDelete's error-path resync can both be in flight at
  // once, and a slower earlier request resolving after a faster later one
  // would otherwise overwrite fresher state with stale data.
  const loadSeq = useRef(0);

  const load = useCallback((opts: { silent?: boolean } = {}) => {
    const seq = ++loadSeq.current;
    if (!opts.silent) setLoading(true);
    Promise.all([listPapers(projectId), getProject(projectId)])
      .then(([ps, detail]) => {
        if (seq !== loadSeq.current) return; // a newer load already won
        setPapers(ps);
        setMyRole(detail.my_role);
      })
      .catch(() => {})
      .finally(() => {
        if (seq === loadSeq.current) setLoading(false);
      });
  }, [projectId]);

  async function handleDelete(paperId: string) {
    setDeleting(paperId);
    try {
      await deletePaper(projectId, paperId);
      setPapers((prev) => prev.filter((p) => p.id !== paperId));
    } catch {
      load({ silent: true });
    } finally {
      setDeleting(null);
    }
  }

  useEffect(() => {
    load();
  }, [load]);

  if (loading) {
    return (
      <div className="space-y-3">
        {[1, 2, 3].map((i) => (
          <div key={i} className="h-20 animate-pulse rounded-xl bg-muted" />
        ))}
      </div>
    );
  }

  const canAdd = myRole !== null && CAN_ADD.includes(myRole);

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          {papers.length === 0
            ? "No papers yet"
            : `${papers.length} paper${papers.length !== 1 ? "s" : ""}`}
        </p>
        {canAdd && (
          <PaperDialog projectId={projectId} onSaved={() => load({ silent: true })}>
            <Button size="sm">
              <Plus className="mr-1.5 size-3.5" />
              Add Paper
            </Button>
          </PaperDialog>
        )}
      </div>

      {papers.length === 0 && (
        <div className="flex flex-col items-center gap-3 py-24 text-center">
          <FileText className="size-8 text-muted-foreground/40" />
          <p className="text-sm text-muted-foreground">
            {canAdd
              ? "Add papers to enable RAG chat on this project."
              : "No papers have been added yet."}
          </p>
        </div>
      )}

      <div className="space-y-2">
        {papers.map((paper) => (
          <div
            key={paper.id}
            className="flex items-start gap-3 rounded-xl border border-border bg-card px-4 py-3"
          >
            <div className="min-w-0 flex-1">
              <p className="line-clamp-1 text-sm font-medium text-foreground">
                {paper.title}
              </p>
              {paper.abstract && (
                <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                  {paper.abstract}
                </p>
              )}
              <p className="mt-1.5 text-xs text-muted-foreground/60">
                {fmtDate(paper.created_at)}
              </p>
            </div>
            {canAdd && (
              <button
                onClick={() => handleDelete(paper.id)}
                disabled={deleting === paper.id}
                className="mt-0.5 shrink-0 rounded p-1 text-muted-foreground/50 transition-colors hover:bg-destructive/10 hover:text-destructive disabled:opacity-40"
                aria-label="Delete paper"
              >
                <Trash2 className="size-3.5" />
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
