"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Clock3, Search, Sparkles, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { SearchInput } from "@/components/ui/search-input";
import { Composer } from "@/components/explorer/composer";
import {
  DEMO_THREAD_ID,
  MOCK_NOW,
  explorations,
  suggestions,
  type ExplorationSummary,
} from "@/lib/explorer-data";
import { formatActivity, formatStarted, outcomeLabel } from "@/lib/explorer";
import { routes } from "@/lib/routes";

/**
 * Explorer's front door: a question box, and the explorations already run.
 *
 * Chat answers from the papers a project already holds; Explorer goes past
 * them and scores what it finds against the library. There is no discovery
 * backend behind it — every question opens the same mock thread, and
 * `lib/explorer-data.ts` is the seam a real service would replace.
 *
 * Nothing is fetched, so there is no loading state to fake. Deleting is local
 * and immediate, matching Chat's no-confirmation delete, and emptying the list
 * is how the empty state is reached.
 */
export default function ExplorerPage() {
  const router = useRouter();
  const [question, setQuestion] = useState("");
  const [query, setQuery] = useState("");
  const [rows, setRows] = useState<ExplorationSummary[]>(explorations);

  const visible = useMemo(
    () => rows.filter((row) => row.title.toLowerCase().includes(query.toLowerCase())),
    [rows, query]
  );

  function submit() {
    router.push(routes.exploration(DEMO_THREAD_ID));
  }

  return (
    <div className="min-h-full">
      <section className="mx-auto flex max-w-4xl flex-col px-4 pb-10 pt-12 sm:px-8">
        <div className="mb-5 flex items-center gap-2">
          <Sparkles aria-hidden="true" className="size-4 text-primary" />
          <h1 className="text-lg font-semibold">Explore beyond your library</h1>
        </div>
        <Composer
          variant="page"
          value={question}
          onChange={setQuestion}
          onSubmit={submit}
          placeholder="What are you trying to find out?"
          minHeightClass="min-h-24"
        />
        <div className="mt-3 flex flex-wrap gap-2">
          {suggestions.map((suggestion) => (
            <Button
              key={suggestion}
              variant="outline"
              size="sm"
              // `max-w-full` because `Button` is `shrink-0`: without it a
              // long suggestion pushes the chip past the viewport on a phone
              // instead of wrapping inside it.
              className="h-auto max-w-full whitespace-normal rounded-full py-1.5 text-left font-normal text-muted-foreground shadow-none"
              onClick={() => setQuestion(suggestion)}
            >
              {suggestion}
            </Button>
          ))}
        </div>
        <p className="mt-4 text-[13px] text-muted-foreground">
          Explorer searches beyond your library and scores every result against it.
        </p>
      </section>

      <section className="border-t bg-card/40">
        <div className="mx-auto max-w-4xl px-4 py-8 sm:px-8">
          <div className="mb-4 flex flex-col justify-between gap-3 sm:flex-row sm:items-center">
            <div>
              <h2 className="font-semibold">Recent explorations</h2>
              <p className="mt-0.5 text-[13px] text-muted-foreground">
                Continue where you left off
              </p>
            </div>
            <div className="w-full sm:w-64">
              <SearchInput
                value={query}
                onChange={setQuery}
                placeholder="Search explorations"
                label="Search explorations"
              />
            </div>
          </div>
          <div className="overflow-hidden rounded-lg border bg-background">
            {visible.length ? (
              visible.map((row) => (
                <div
                  key={row.id}
                  className="group relative flex items-start gap-3 border-b p-4 transition-colors last:border-0 hover:bg-accent/60 focus-within:bg-accent/60"
                >
                  <span
                    aria-hidden="true"
                    className="mt-0.5 grid size-8 shrink-0 place-items-center rounded-md bg-secondary"
                  >
                    <Clock3 className="size-4 text-muted-foreground" />
                  </span>
                  <div className="min-w-0 flex-1">
                    {/* The link's ::after covers the whole row, so the row is
                        clickable without nesting the delete button inside an
                        anchor — which is invalid, and which a keyboard reaches
                        as one confused stop. */}
                    <Link
                      href={routes.exploration(row.id)}
                      // `block` is what makes `truncate` work at all: an <a>
                      // is inline, and `overflow: hidden` does nothing on an
                      // inline box, so a long question ran off the row.
                      className="block truncate text-sm font-medium after:absolute after:inset-0 after:rounded-none focus-visible:outline-none focus-visible:after:ring-1 focus-visible:after:ring-inset focus-visible:after:ring-ring"
                    >
                      {row.title}
                    </Link>
                    <p className="mt-1 text-xs text-muted-foreground">
                      Started {formatStarted(row.started, MOCK_NOW)} · Last activity{" "}
                      {formatActivity(row.activity, MOCK_NOW)}
                    </p>
                    <p className="mt-1 text-[13px] text-muted-foreground">
                      {outcomeLabel(row)}
                    </p>
                  </div>
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={`Delete ${row.title}`}
                    className="relative z-10 size-8 shrink-0 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100"
                    onClick={() =>
                      setRows((current) => current.filter((r) => r.id !== row.id))
                    }
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </div>
              ))
            ) : (
              <div className="grid min-h-52 place-items-center p-8 text-center">
                <div>
                  <Search
                    aria-hidden="true"
                    className="mx-auto mb-3 size-5 text-muted-foreground"
                  />
                  <p className="font-medium">No explorations here</p>
                  <p className="mt-1 text-[13px] text-muted-foreground">
                    Start a question above to create one.
                  </p>
                </div>
              </div>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}
