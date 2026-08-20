"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { LatexWorkspace } from "@/components/latex/latex-workspace";
import { RxTheme } from "@/components/rx-theme";
import { getProject } from "@/lib/projects";
import type { Role } from "@/lib/types";
import "./latex.css";

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

  // `RxTheme` scopes the Institution/Console palette and the concept's three
  // typefaces to this subtree and nothing else -- Chat and every other tab
  // keep the app's own tokens and keep rendering in Inter. `rx-tex` carries
  // what is this screen's alone, the 1760px shell cap above all.
  return (
    <RxTheme className="rx-tex">
      <div className="rx-shell">
        {error ? (
          <div className="rx-tex-empty" style={{ color: "oklch(var(--destructive))" }}>
            {error}
          </div>
        ) : role === null ? (
          <div className="rx-tex-skel" />
        ) : (
          <LatexWorkspace projectId={projectId} role={role} />
        )}
      </div>
    </RxTheme>
  );
}
