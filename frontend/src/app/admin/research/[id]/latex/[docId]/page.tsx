"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { LatexWorkspace } from "@/components/latex/latex-workspace";
import { getProject } from "@/lib/projects";

export default function LatexDocumentPage() {
  const { id: projectId, docId } = useParams<{ id: string; docId: string }>();
  // Access to THIS document is decided per document (`my_access`, resolved
  // inside `LatexWorkspace`) -- the project role has nothing left to gate
  // here. What this page still needs from the project is the owner's id, to
  // hand the share dialog the one person a grant can never name alongside
  // the document's own creator.
  const [ownerId, setOwnerId] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId) return;
    let cancelled = false;
    setError(null);
    getProject(projectId)
      .then((detail) => {
        if (cancelled) return;
        setOwnerId(detail.members.find((m) => m.role === "owner")?.user.id ?? null);
        setLoaded(true);
      })
      .catch(() => {
        // Without this, a failed request leaves `loaded` false forever and
        // this page shows the loading skeleton -- not a wrong project, just
        // an eternal spinner with no way out and nothing telling the user
        // why.
        if (!cancelled) setError("Could not load this project. Please try again.");
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  // Both states take the editor frame's own box (the prototype's `75vh`,
  // `rounded-lg`), so the page does not jump when the workspace lands.
  if (error) {
    return (
      <div className="flex h-[75vh] items-center justify-center rounded-lg border text-[13px] text-destructive">
        {error}
      </div>
    );
  }

  if (!loaded) {
    return <div className="h-[75vh] animate-pulse rounded-lg bg-muted" />;
  }

  return <LatexWorkspace projectId={projectId} documentId={docId} ownerId={ownerId} />;
}
