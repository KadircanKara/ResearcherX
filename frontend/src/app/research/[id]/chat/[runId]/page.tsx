"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { RunStream } from "@/components/run-stream";

export default function RunPage() {
  const { id: projectId, runId } = useParams<{ id: string; runId: string }>();

  return (
    <div>
      <Link
        href={`/research/${projectId}/chat`}
        className="mb-4 inline-flex items-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground"
      >
        <ArrowLeft className="size-3.5" />
        All research
      </Link>
      <RunStream runId={runId} />
    </div>
  );
}
