"use client";

import { useState } from "react";
import { ExplorationList } from "@/components/explorer/exploration-list";
import { ExplorerEmpty } from "@/components/explorer/explorer-empty";
import { EXPLORATIONS, type Exploration } from "@/lib/explorer-data";

/**
 * Explorer's history, mirroring Chat's conversation list at
 * `/research/[id]/chat`: a list here, one thread at `/explorer/[eid]`.
 *
 * Unlike Chat there is nothing to fetch — the corpus is a static module — so
 * there is no loading skeleton to show rather than a fake one. Deleting is
 * local and immediate, matching Chat's no-confirmation delete, and emptying the
 * list is how the empty state is reached.
 */
export default function ExplorerPage() {
  const [explorations, setExplorations] = useState<Exploration[]>(EXPLORATIONS);

  if (explorations.length === 0) return <ExplorerEmpty />;

  return (
    <ExplorationList
      explorations={explorations}
      onDelete={(id) => setExplorations((prev) => prev.filter((e) => e.id !== id))}
    />
  );
}
