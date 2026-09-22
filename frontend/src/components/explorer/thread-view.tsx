"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowLeft, PanelRight, RefreshCw, Square } from "lucide-react";
import Link from "next/link";
import { Answer } from "@/components/explorer/answer";
import { CandidateCard } from "@/components/explorer/candidate-card";
import { Composer } from "@/components/explorer/composer";
import { ProcessTrail } from "@/components/explorer/process-trail";
import { Rail } from "@/components/explorer/rail";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import {
  REPLAY_STAGE_DONE,
  REPLAY_STAGE_PLANNING,
  replaySchedule,
} from "@/lib/explorer";
import type { Candidate, ExplorationThread } from "@/lib/explorer-data";
import { routes } from "@/lib/routes";

/**
 * The user's own question. `@`-mentioned papers are rendered as chips: the
 * message text is the literal string that was typed, and the chip is derived
 * from it on read — nothing records which substring belonged to which paper.
 * (The same split Chat makes; see the mentions rule in CLAUDE.md.)
 */
function Question({ text }: { text: string }) {
  const parts = text.split(/(@[^?.,]+)/g);
  return (
    <div className="mb-5 ml-auto max-w-[82%] rounded-lg border bg-secondary/60 px-4 py-3 text-sm leading-relaxed">
      {parts.map((part, index) =>
        part.startsWith("@") ? (
          <span
            key={`${part}-${index}`}
            className="mx-1 inline-flex rounded-full bg-background px-2 py-0.5 text-xs font-medium text-primary"
          >
            {part}
          </span>
        ) : (
          part
        )
      )}
    </div>
  );
}

/** The turn Replay re-runs: the last one, which is where the reader is. */
function replayTurnIndex(thread: ExplorationThread): number {
  return thread.turns.length - 1;
}

export function ThreadView({ thread }: { thread: ExplorationThread }) {
  const [added, setAdded] = useState<string[]>([]);
  const [draft, setDraft] = useState("");
  // `null` when nothing is being replayed; otherwise the stage the replay has
  // reached. The two are one piece of state because a stage without a replay
  // is meaningless, and a replay without a stage cannot render.
  const [stage, setStage] = useState<number | null>(null);
  const [stream, setStream] = useState("");
  const timers = useRef<number[]>([]);

  function clearTimers() {
    timers.current.forEach((id) => window.clearTimeout(id));
    timers.current = [];
  }

  function stop() {
    clearTimers();
    setStage(null);
    setStream("");
  }

  function start() {
    clearTimers();
    const turn = thread.turns[replayTurnIndex(thread)];
    if (!turn) return;

    setStage(REPLAY_STAGE_PLANNING);
    setStream("");
    // The whole run is data (`replaySchedule`), so this loop is the only
    // timing code: one timer per frame, every handle kept so Stop and unmount
    // can cancel them.
    for (const frame of replaySchedule(turn.answer)) {
      timers.current.push(
        window.setTimeout(() => {
          if (frame.stage !== undefined) setStage(frame.stage);
          if (frame.text !== undefined) setStream(frame.text);
        }, frame.at)
      );
    }
  }

  // Timers outlive React's render, so leaving this out leaks a setState into an
  // unmounted tree every time the reader navigates away mid-replay.
  useEffect(() => clearTimers, []);

  const running = stage !== null && stage < REPLAY_STAGE_DONE;
  const replayIndex = replayTurnIndex(thread);
  const candidates = thread.turns.flatMap((turn) => turn.candidates);
  const addPaper = (paper: Candidate) =>
    setAdded((current) =>
      current.includes(paper.id) ? current : [...current, paper.id]
    );
  const rail = <Rail thread={thread} added={added} candidates={candidates} />;

  return (
    <div className="min-h-full">
      {/* `top-12` clears the app shell's own sticky topbar, which is h-12. */}
      <header className="sticky top-12 z-20 flex h-14 items-center gap-2 border-b bg-background/95 px-4 backdrop-blur">
        <Link
          href={routes.explorer()}
          aria-label="All explorations"
          className="shrink-0 rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <ArrowLeft className="size-4" />
        </Link>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold">{thread.title}</p>
          <p className="hidden text-[11px] text-muted-foreground sm:block">
            {thread.project}
          </p>
        </div>
        <Sheet>
          <SheetTrigger
            render={
              <Button variant="outline" size="sm" className="rounded-full xl:hidden" />
            }
          >
            <PanelRight />
            Details
          </SheetTrigger>
          <SheetContent side="bottom">
            <SheetTitle>Exploration details</SheetTitle>
            {rail}
          </SheetContent>
        </Sheet>
        {running ? (
          <Button variant="outline" size="sm" className="rounded-full" onClick={stop}>
            <Square className="fill-current" />
            Stop
          </Button>
        ) : (
          <Button variant="outline" size="sm" className="rounded-full" onClick={start}>
            <RefreshCw />
            Replay
          </Button>
        )}
      </header>

      <div className="grid xl:grid-cols-[minmax(0,1fr)_320px]">
        {/* `min-w-0` is load-bearing: a grid item's automatic minimum size is
            its MIN-CONTENT, and the source-card row below is a horizontal
            scroller whose cards have a 13rem floor. Three of them put this
            column's min-content at 672px, so without this the whole page
            scrolled sideways on a phone instead of just that one row. */}
        <main className="mx-auto w-full min-w-0 max-w-[824px] px-4 pb-32 pt-8 sm:px-8">
          {thread.turns.map((turn, turnIndex) => {
            const replaying = stage !== null && turnIndex === replayIndex;
            // Everything after the answer is withheld while the turn is being
            // re-run: candidates and follow-ups are conclusions, and showing
            // them beside a half-written answer would give away the ending.
            const settled = !(replaying && stage < REPLAY_STAGE_DONE);
            return (
              <section key={turn.question} className="mb-14">
                <Question text={turn.question} />
                <div className="space-y-5">
                  <ProcessTrail turn={turn} replayStage={replaying ? stage : null} />
                  <Answer turn={turn} streamingText={settled ? undefined : stream} />
                  {settled ? (
                    <>
                      <div>
                        <h3 className="mb-3 text-sm font-semibold">
                          Papers worth adding
                        </h3>
                        <div className="space-y-3">
                          {turn.candidates.map((paper) => (
                            <CandidateCard
                              key={paper.id}
                              paper={paper}
                              added={added.includes(paper.id)}
                              onAdd={() => addPaper(paper)}
                            />
                          ))}
                        </div>
                      </div>
                      <div className="flex flex-wrap gap-2">
                        {turn.followUps.map((suggestion) => (
                          <Button
                            key={suggestion}
                            variant="outline"
                            size="sm"
                            // `max-w-full` because `Button` is `shrink-0`; see
                            // the same note on the home page's suggestions.
                            className="h-auto max-w-full whitespace-normal rounded-full py-1.5 text-left font-normal"
                            onClick={() => setDraft(suggestion)}
                          >
                            {suggestion}
                          </Button>
                        ))}
                      </div>
                    </>
                  ) : null}
                </div>
              </section>
            );
          })}
          {/* Nothing to send to: there is no discovery backend, so submitting
              replays the last turn rather than pretending to ask a new one. */}
          <Composer value={draft} onChange={setDraft} onSubmit={start} />
        </main>
        <aside className="hidden border-l bg-card/30 p-4 xl:block">
          <div className="sticky top-[104px]">{rail}</div>
        </aside>
      </div>
    </div>
  );
}
