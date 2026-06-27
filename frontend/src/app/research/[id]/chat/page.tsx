"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { ArrowRight, Loader2, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { createRun } from "@/lib/api";
import { listProjectRuns } from "@/lib/projects";
import type { Run } from "@/lib/types";

const STATUS_CONFIG = {
  pending:   { label: "Pending",   cls: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400" },
  running:   { label: "Running",   cls: "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400" },
  completed: { label: "Done",      cls: "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400" },
  failed:    { label: "Failed",    cls: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400" },
} as const;

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

export default function ChatPage() {
  const { id: projectId } = useParams<{ id: string }>();
  const router = useRouter();

  const [runs, setRuns] = useState<Run[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [question, setQuestion] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  useEffect(() => {
    listProjectRuns(projectId)
      .then(setRuns)
      .catch(() => {
        // layout.tsx handles 404/403 for the project itself
      })
      .finally(() => setLoading(false));
  }, [projectId]);

  async function handleSubmit() {
    const q = question.trim();
    if (!q || submitting) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const run = await createRun(q, projectId);
      setSubmitting(false);
      router.push(`/research/${projectId}/chat/${run.id}`);
    } catch {
      setSubmitError("Failed to start research. Please try again.");
      setSubmitting(false);
    }
  }

  if (loading) {
    return (
      <div className="space-y-3 py-4">
        {[1, 2, 3].map((i) => (
          <div key={i} className="h-16 animate-pulse rounded-xl bg-muted" />
        ))}
      </div>
    );
  }

  return (
    <div>
      {/* Header row */}
      <div className="mb-4 flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          {runs.length === 0
            ? "No research yet"
            : `${runs.length} run${runs.length !== 1 ? "s" : ""}`}
        </p>
        {!showForm && (
          <Button size="sm" onClick={() => setShowForm(true)}>
            <Plus className="mr-1.5 size-3.5" />
            New Research
          </Button>
        )}
      </div>

      {/* Inline query form */}
      {showForm && (
        <div className="mb-4 rounded-xl border border-border bg-card p-4">
          <textarea
            autoFocus
            rows={3}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                handleSubmit();
              }
            }}
            placeholder="What do you want to research?"
            className="w-full resize-none bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          />
          {submitError && (
            <p className="mt-1 text-xs text-destructive">{submitError}</p>
          )}
          <div className="mt-3 flex gap-2">
            <Button
              size="sm"
              onClick={handleSubmit}
              disabled={!question.trim() || submitting}
            >
              {submitting ? (
                <Loader2 className="mr-1.5 size-3.5 animate-spin" />
              ) : (
                <ArrowRight className="mr-1.5 size-3.5" />
              )}
              Research
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setShowForm(false);
                setQuestion("");
                setSubmitError(null);
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      )}

      {/* Empty state */}
      {runs.length === 0 && !showForm && (
        <div className="flex flex-col items-center gap-2 py-24 text-center">
          <p className="text-sm text-muted-foreground">
            Start your first research run to see results here.
          </p>
        </div>
      )}

      {/* Run list */}
      <div className="space-y-2">
        {runs.map((run) => {
          const sc = STATUS_CONFIG[run.status] ?? STATUS_CONFIG.pending;
          return (
            <button
              key={run.id}
              type="button"
              onClick={() =>
                router.push(`/research/${projectId}/chat/${run.id}`)
              }
              className="group flex w-full items-start gap-3 rounded-xl border border-border bg-card px-4 py-3 text-left transition-colors hover:bg-muted"
            >
              <div className="min-w-0 flex-1">
                <p className="line-clamp-2 text-sm font-medium text-foreground">
                  {run.question}
                </p>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {fmtDate(run.created_at)}
                </p>
              </div>
              <span
                className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ${sc.cls}`}
              >
                {sc.label}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
