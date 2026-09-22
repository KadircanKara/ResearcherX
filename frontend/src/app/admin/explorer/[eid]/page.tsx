"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { ThreadView } from "@/components/explorer/thread-view";
import { ThemeToggle } from "@/components/theme-toggle";
import { getExplorationThread } from "@/lib/explorer-data";
import { routes } from "@/lib/routes";

/**
 * One exploration, the counterpart of `/research/[id]/chat/[cid]`. The corpus
 * is a static module, so a missing id is resolved synchronously and there is no
 * loading state to fake.
 */
export default function ExplorationPage() {
  const { eid } = useParams<{ eid: string }>();
  const thread = getExplorationThread(eid);

  if (!thread) {
    return (
      <>
        <div className="hidden h-14 items-center justify-end border-b px-6 lg:flex">
          <ThemeToggle />
        </div>
        <div className="mx-auto max-w-4xl px-4 py-8 sm:px-8">
          <Link
            href={routes.explorer()}
            className="inline-flex items-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground"
          >
            <ArrowLeft aria-hidden="true" className="size-3.5" />
            All explorations
          </Link>
          <p className="py-8 text-sm text-muted-foreground">Exploration not found.</p>
        </div>
      </>
    );
  }

  return <ThreadView thread={thread} />;
}
