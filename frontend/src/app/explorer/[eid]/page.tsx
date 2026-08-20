"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { ExplorationThread } from "@/components/explorer/exploration-thread";
import { findExploration } from "@/lib/explorer-data";

/**
 * One exploration, the counterpart of `/research/[id]/chat/[cid]`. Same back
 * link, same not-found treatment — the corpus is static, so a missing id is
 * resolved synchronously and there is no loading state to fake.
 */
export default function ExplorationPage() {
  const { eid } = useParams<{ eid: string }>();
  const exploration = findExploration(eid);

  if (!exploration) {
    return (
      <div className="rx-shell">
        <div className="rx-head">
          <Link href="/explorer" className="rx-backlink">
            <ArrowLeft className="size-3" aria-hidden="true" />
            All explorations
          </Link>
        </div>
        <p className="py-8 text-sm text-muted-foreground">Exploration not found.</p>
      </div>
    );
  }

  return <ExplorationThread exploration={exploration} />;
}
