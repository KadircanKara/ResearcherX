"use client";

import { useState } from "react";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { CandidateRow } from "@/components/explorer/candidate-row";
import { Composer } from "@/components/explorer/composer";
import {
  exchangesLabel,
  formatDistance,
  startedLabel,
  threadOutcome,
} from "@/lib/explorer";
import { MOCK_NOW, type Candidate, type Exploration } from "@/lib/explorer-data";

const THREADED_EXPLORATION_TITLE = "Field evidence for coverage under vehicle loss";

const ADDED_NOTE = "Ingesting now. It joins Papers, the Graph and your next question on its own.";

/**
 * One exploration: prose that argues, candidates that hang under the paragraph
 * arguing for them, and a rail saying what everything was scored against.
 *
 * The rail folds below the prose at 1080px and the reading measure never grows
 * — extra width is spent on the rail, never on longer lines.
 */
export function ExplorationThread({ exploration }: { exploration: Exploration }) {
  // Adding is real local state, not a faked request: the row moves into the
  // library treatment and the rail picks it up. Nothing is sent anywhere.
  const [addedHere, setAddedHere] = useState<Candidate[]>([]);

  function handleAdd(candidate: Candidate) {
    setAddedHere((prev) =>
      prev.some((c) => c.id === candidate.id) ? prev : [...prev, candidate]
    );
  }

  const addedFrom = [
    ...exploration.addedFrom,
    ...addedHere
      .filter((c) => !exploration.addedFrom.some((a) => a.title === c.title))
      .map((c) => ({ title: c.title, distance: c.distance ?? 0, note: ADDED_NOTE })),
  ];

  const embeddedPapers = exploration.scoredAgainst.length;

  return (
    <div className="rx-shell">
      <div className="rx-exgrid">
        <div className="rx-head">
          <div>
            <Link href="/explorer" className="rx-backlink">
              <ArrowLeft className="size-3" aria-hidden="true" />
              All explorations
            </Link>
            <h1>{exploration.title}</h1>
          </div>
          <div className="rx-meta">
            {exchangesLabel(exploration.exchanges)} · started{" "}
            {startedLabel(exploration.startedAt, MOCK_NOW)}
            <br />
            {threadOutcome(exploration.added, exploration.considered)}
          </div>
        </div>

        <div className="rx-column">
          {exploration.turns.length === 0 ? (
            <div className="rx-empty">
              <h2>This exploration is not part of the design preview.</h2>
              <p>
                Only “{THREADED_EXPLORATION_TITLE}” carries a full thread in the
                approved concept. The row above is its real list metadata; the
                exchange behind it will arrive with the discovery service.
              </p>
            </div>
          ) : (
            exploration.turns.map((turn) => (
              <article className="rx-turn" key={turn.n}>
                <div className="rx-user-row">
                  <div className="rx-bub-user">
                    {turn.question.map((part, i) =>
                      part.mention ? (
                        <span className="rx-mention" key={i}>
                          {part.text}
                        </span>
                      ) : (
                        <span key={i}>{part.text}</span>
                      )
                    )}
                  </div>
                </div>
                <div className="rx-answer">
                  {turn.blocks.map((block, i) =>
                    block.kind === "prose" ? (
                      <p key={i}>{block.text}</p>
                    ) : (
                      <CandidateRow
                        key={block.candidate.id}
                        candidate={block.candidate}
                        held={
                          block.candidate.held ||
                          addedHere.some((c) => c.id === block.candidate.id)
                        }
                        onAdd={handleAdd}
                      />
                    )
                  )}
                </div>
              </article>
            ))
          )}

          <div className="rx-newq rx-thread-composer">
            <Composer
              label="Push back on this answer"
              placeholder="Only ones published after 2024, and none that need a motion-capture rig"
              submitLabel="Ask"
            />
          </div>
        </div>

        <aside className="rx-rail" aria-label="What candidates are scored against">
          <div className="rx-flank-h">Scored against</div>
          <div className="rx-tot">{embeddedPapers} embedded papers</div>
          <ul className="rx-fl-list">
            {exploration.scoredAgainst.map((p) => (
              <li key={p.title}>
                <span className="rx-ttl">{p.title}</span>
                <span className="rx-d">{p.chunks}</span>
              </li>
            ))}
          </ul>
          <p className="rx-flank-note">{exploration.railNote}</p>

          {addedFrom.length > 0 && (
            <>
              <div className="rx-flank-h rx-flank-h-next">
                Added from this exploration
              </div>
              <ul className="rx-fl-list">
                {addedFrom.map((p) => (
                  <li key={p.title}>
                    <span className="rx-ttl">{p.title}</span>
                    <span className="rx-d">{formatDistance(p.distance)}</span>
                    <span className="rx-why">{p.note}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </aside>
      </div>
    </div>
  );
}
