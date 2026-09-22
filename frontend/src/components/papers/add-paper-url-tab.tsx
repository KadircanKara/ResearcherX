"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  ITEM_TIMEOUT_MS,
  TITLE_MAX,
  linkFailure,
  withTimeout,
} from "@/lib/paper-batch";
import {
  createPaper,
  deletePaper,
  ingestPaperFromUrl,
  suggestTitleFromUrl,
} from "@/lib/projects";

/**
 * URL: fetch a suggested title for one link, then create the paper and
 * ingest the PDF the link serves.
 */
export function AddPaperUrlTab({
  projectId,
  onSaved,
  onDone,
  onBusyChange,
}: {
  projectId: string;
  onSaved: () => void;
  onDone: () => void;
  onBusyChange: (busy: boolean) => void;
}) {
  const [url, setUrl] = useState("");
  const [title, setTitle] = useState("");
  const [abstract, setAbstract] = useState<string | null>(null);
  const [fetching, setFetching] = useState(false);
  const [fetched, setFetched] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // An edit to the URL while a fetch is in flight makes that fetch's answer
  // a title for a link that is no longer in the field.
  const fetchSeq = useRef(0);

  useEffect(() => {
    onBusyChange(fetching || submitting);
  }, [fetching, submitting, onBusyChange]);

  async function fetchTitle() {
    const target = url.trim();
    if (!target) return;
    const seq = ++fetchSeq.current;
    setFetching(true);
    setFetched(false);
    setError(null);
    let suggested: string | null = null;
    let suggestedAbstract: string | null = null;
    try {
      const meta = await withTimeout(
        suggestTitleFromUrl(projectId, target),
        ITEM_TIMEOUT_MS,
        "suggestTitleFromUrl"
      );
      suggested = meta.title;
      suggestedAbstract = meta.abstract;
    } catch {
      // Fail open: the link itself is an editable stand-in title.
    }
    if (seq !== fetchSeq.current) return;
    setTitle((suggested ?? target).slice(0, TITLE_MAX));
    setAbstract(suggestedAbstract);
    setFetching(false);
    setFetched(true);
  }

  async function submit() {
    const target = url.trim();
    if (!fetched || !title.trim() || !target || submitting) return;
    setSubmitting(true);
    setError(null);
    let paperId: string | null = null;
    try {
      const paper = await withTimeout(
        createPaper(projectId, {
          title: title.trim(),
          abstract,
          body: null,
          pdf_url: target,
          source: "link",
        }),
        ITEM_TIMEOUT_MS,
        "createPaper"
      );
      paperId = paper.id;
      await withTimeout(
        ingestPaperFromUrl(projectId, paper.id, target),
        ITEM_TIMEOUT_MS,
        "ingestPaperFromUrl"
      );
      setSubmitting(false);
      onSaved();
      onDone();
    } catch (e) {
      // A paywalled link is a normal outcome here, not a crash. A failed
      // compensating delete is said distinctly: the leftover row can only be
      // fixed by deleting it, so the reader has to know to go find it.
      let cleanedUp = true;
      if (paperId) {
        try {
          await deletePaper(projectId, paperId);
        } catch (cleanupErr) {
          cleanedUp = false;
          console.error("paper cleanup failed after ingest error", { paperId, cleanupErr });
        }
      }
      const flags = e as Error & { paywalled?: boolean; unavailable?: boolean };
      setError(
        linkFailure({
          cleanedUp,
          paywalled: Boolean(flags?.paywalled),
          unavailable: Boolean(flags?.unavailable),
        })
      );
      setSubmitting(false);
      if (!cleanedUp) onSaved();
    }
  }

  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        <Label htmlFor="url-field">URL</Label>
        <div className="flex gap-2">
          <Input
            id="url-field"
            placeholder="https://…"
            value={url}
            disabled={submitting}
            onChange={(e) => {
              setUrl(e.target.value);
              setFetched(false);
              setError(null);
              if (fetching) {
                fetchSeq.current += 1;
                setFetching(false);
              }
            }}
          />
          <Button
            type="button"
            variant="outline"
            disabled={!url.trim() || fetching || submitting}
            onClick={() => void fetchTitle()}
          >
            {fetching ? "Fetching…" : "Fetch"}
          </Button>
        </div>
      </div>
      {fetched && (
        <div className="fade-block space-y-1.5">
          <Label htmlFor="url-title">Title</Label>
          <Input
            id="url-title"
            value={title}
            maxLength={TITLE_MAX}
            disabled={submitting}
            onChange={(e) => setTitle(e.target.value)}
          />
        </div>
      )}
      {error && <p className="text-[12px] text-destructive">{error}</p>}
      <div className="flex justify-end">
        <Button disabled={!fetched || !title.trim() || submitting} onClick={() => void submit()}>
          {submitting ? "Adding…" : "Add paper"}
        </Button>
      </div>
    </div>
  );
}
