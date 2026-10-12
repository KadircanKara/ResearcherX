"use client";

import { useParams } from "next/navigation";
import { LatexWorkspace } from "@/components/latex/latex-workspace";

export default function LatexDocumentPage() {
  // Access to THIS document is decided per document (`my_access`, resolved
  // inside `LatexWorkspace`). The demo has no document sharing, so nothing
  // else is needed from the project here.
  const { id: projectId, docId } = useParams<{ id: string; docId: string }>();
  return <LatexWorkspace projectId={projectId} documentId={docId} />;
}
