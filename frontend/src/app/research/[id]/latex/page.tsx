"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { LatexWorkspace } from "@/components/latex/latex-workspace";
import { getProject } from "@/lib/projects";
import type { Role } from "@/lib/types";

export default function LaTeXPage() {
  const { id: projectId } = useParams<{ id: string }>();
  const [role, setRole] = useState<Role | null>(null);

  useEffect(() => {
    if (!projectId) return;
    getProject(projectId).then((detail) => setRole(detail.my_role));
  }, [projectId]);

  if (role === null) {
    return <div className="h-[70vh] animate-pulse rounded-xl bg-muted" />;
  }

  return <LatexWorkspace projectId={projectId} role={role} />;
}
