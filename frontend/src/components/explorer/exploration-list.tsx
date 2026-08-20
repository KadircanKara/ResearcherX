"use client";

import Link from "next/link";
import { Trash2 } from "lucide-react";
import { Composer } from "@/components/explorer/composer";
import {
  exchangesLabel,
  formatActivity,
  listSummary,
  outcomeLabel,
} from "@/lib/explorer";
import { MOCK_NOW, type Exploration } from "@/lib/explorer-data";

/**
 * The exploration history — the same shape Chat's conversation list has, so a
 * user does not have to learn two histories in one product: a list, a composer
 * that starts a new one, and a per-row delete with no confirmation step.
 */
export function ExplorationList({
  explorations,
  onDelete,
}: {
  explorations: Exploration[];
  onDelete: (id: string) => void;
}) {
  return (
    <div className="rx-shell">
      <div className="rx-head">
        <div>
          <div className="rx-eyebrow">Explorer</div>
          <h1>Explorations</h1>
        </div>
        <div className="rx-meta">
          {listSummary(explorations)}
          <br />
          You own this project, so you can delete any of them
        </div>
      </div>

      <p className="rx-lede">
        Describe what your library is missing and argue with the answer until it
        narrows. Each exploration keeps its own thread, and anything you add from
        one is ingested like an upload.
      </p>

      <div className="rx-newq">
        <Composer
          label="Start a new exploration"
          placeholder="Papers on radio loss in aerial swarms, published after 2024"
          submitLabel="Start exploring"
        />
      </div>

      <div className="rx-clist rx-cgrid">
        <div className="rx-ccols">
          <span>Exploration</span>
          <span>Outcome</span>
          <span>Length</span>
          <span>Last activity</span>
          <span />
        </div>

        {explorations.map((e) => (
          // A div, not a link wrapping everything: the delete control is a real
          // button, and nesting interactive content is invalid HTML. The row's
          // hit area comes from the link's ::after overlay instead.
          <div className="rx-crow" key={e.id}>
            <Link href={`/explorer/${e.id}`} className="rx-copen">
              <span className="rx-ct">{e.title}</span>
              <span className="rx-cq">Last asked: “{e.lastAsked}”</span>
            </Link>
            <span className="rx-cmeta">
              <span className="rx-cscope">{outcomeLabel(e.added)}</span>
              <span className="rx-cn">{exchangesLabel(e.exchanges)}</span>
              <span className="rx-cd">
                {formatActivity(e.lastActivityAt, MOCK_NOW)}
              </span>
            </span>
            <button
              type="button"
              className="rx-cdel"
              aria-label={`Delete “${e.title}”`}
              onClick={() => onDelete(e.id)}
            >
              <Trash2 className="size-3.5" aria-hidden="true" />
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
