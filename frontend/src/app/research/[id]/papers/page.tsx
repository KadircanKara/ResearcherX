"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { FileText, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { AddPaperDialog } from "@/components/add-paper-dialog";
import { getProject, listPapers } from "@/lib/projects";
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

  const load = useCallback(() => {
    setLoading(true);
    Promise.all([listPapers(projectId), getProject(projectId)])
      .then(([ps, detail]) => {
        setPapers(ps);
        setMyRole(detail.my_role);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [projectId]);

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
          <AddPaperDialog projectId={projectId} onAdded={load}>
            <Button size="sm">
              <Plus className="mr-1.5 size-3.5" />
              Add Paper
            </Button>
          </AddPaperDialog>
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
            className="rounded-xl border border-border bg-card px-4 py-3"
          >
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
        ))}
      </div>
    </div>
  );
}
