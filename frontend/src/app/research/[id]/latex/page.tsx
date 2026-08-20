"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { LatexWorkspace } from "@/components/latex/latex-workspace";
import { getProject } from "@/lib/projects";
import type { Role } from "@/lib/types";

export default function LaTeXPage() {
  const { id: projectId } = useParams<{ id: string }>();
  const [role, setRole] = useState<Role | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId) return;
    let cancelled = false;
    setError(null);
    getProject(projectId)
      .then((detail) => {
        if (!cancelled) setRole(detail.my_role);
      })
      .catch(() => {
        // Without this, a failed request leaves `role` null forever and
        // this page shows the loading skeleton -- not a wrong project, just
        // an eternal spinner with no way out and nothing telling the user
        // why.
        if (!cancelled) setError("Could not load this project. Please try again.");
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  if (error) {
    return (
      <div className="flex h-[70vh] items-center justify-center text-sm text-destructive">
        {error}
      </div>
    );
  }

  if (role === null) {
    return <div className="h-[70vh] animate-pulse rounded-xl bg-muted" />;
  }

  return <LatexWorkspace projectId={projectId} role={role} />;
}
