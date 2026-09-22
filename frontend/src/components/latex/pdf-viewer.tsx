"use client";

import { useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { FileWarning, Loader2 } from "lucide-react";
import {
  canvasToTex,
  texToPercent,
  type CompileStatus,
  type TexPoint,
} from "@/lib/latex-sync";
import { cn } from "@/lib/utils";
// Type-only: erased at build time, so this does not touch the module-scope
// import restriction below (pdf.js itself is still loaded only inside the
// effect).
import type { PDFDocumentLoadingTask, RenderTask } from "pdfjs-dist";

export interface PdfHighlight {
  page: number;
  x: number;
  y: number;
  width: number;
  height: number;
}

interface PdfViewerProps {
  bytes: Uint8Array | null;
  /** What to say when there is no PDF yet: compiling, failed, or never built. */
  status: CompileStatus;
  /**
   * PDF-only mode. Pages there may grow to `max-w-3xl`; beside the source
   * they keep the prototype's `max-w-md` card width.
   */
  wide: boolean;
  highlight: PdfHighlight | null;
  scrollToPage: number | null;
  onPageDoubleClick: (page: number, point: TexPoint) => void;
}

interface RenderedPage {
  pageNumber: number;
  /** TeX big points -- the page at `scale: 1`. Every coordinate is relative to these. */
  width: number;
  height: number;
}

/**
 * The CSS width every page is DRAWN at: the widest a page is ever shown
 * (`max-w-3xl`, 48rem). A narrower pane scales the canvas down with CSS,
 * never re-renders it -- so dragging the file tree's seam costs nothing, and
 * the backing store is still sharp at the largest size it can appear.
 */
const RENDER_CSS_WIDTH = 768;

export function PdfViewer({
  bytes,
  status,
  wide,
  highlight,
  scrollToPage,
  onPageDoubleClick,
}: PdfViewerProps) {
  const [pages, setPages] = useState<RenderedPage[]>([]);
  const [error, setError] = useState<string | null>(null);
  const canvasRefs = useRef<Map<number, HTMLCanvasElement>>(new Map());
  // Bumped on every render pass so a slower earlier PDF cannot paint over a
  // faster later one -- the same out-of-order guard papers/page.tsx uses.
  const renderSeq = useRef(0);
  // In-flight RenderTask per page. pdf.js tracks "this canvas is mid-render"
  // in its own internal WeakSet, keyed by canvas element -- since canvasRefs
  // reuses one <canvas> per page number across passes, a task from a
  // superseded pass that is never explicitly cancelled leaves that entry set
  // forever, and the next pass's render() on the same canvas throws "Cannot
  // use the same canvas during multiple render() operations." `cancelled`/
  // `renderSeq` stop this component from *acting* on a stale pass, but only
  // task.cancel() tells pdf.js the canvas is free again.
  const renderTasks = useRef<Map<number, RenderTask>>(new Map());
  // This pass's document-loading task, kept so it can be destroy()'d.
  // Confirmed against the installed pdfjs-dist@4.8.69 source: getDocument()
  // constructs exactly one `new PDFWorker`, and the only path that ever
  // terminates it is `loadingTask.destroy()` -> `PDFWorker.destroy()` ->
  // `this._webWorker.terminate()` (its own doc comment: "Abort all network
  // requests and destroy the worker"). Discarding the task, as before,
  // discarded the only handle capable of ever freeing that worker -- a long
  // session leaked one live Worker and one parsed document per compile.
  const loadingTask = useRef<PDFDocumentLoadingTask | null>(null);

  useEffect(() => {
    if (!bytes) {
      setPages([]);
      return;
    }
    const seq = ++renderSeq.current;
    // Cleared at the START of the pass, not just on success at the end --
    // otherwise a pass that is still loading, or one that fails again, goes
    // on describing whatever the PREVIOUS pass's error said for the pass's
    // entire duration (see the always-mounted container below for the other
    // half of this fix -- clearing this alone is not enough, because a
    // pass that never gets here to clear it must not have starved itself of
    // somewhere to draw either).
    setError(null);
    let cancelled = false;
    // Captured once: renderTasks.current is a stable Map for the component's
    // whole lifetime (only ever mutated via set/delete/clear, never
    // reassigned), so this and `renderTasks.current` are always the same
    // object -- but referencing the ref itself from the cleanup below trips
    // react-hooks/exhaustive-deps ("might have changed by the time cleanup
    // runs"), which is written for refs that get reattached to a new DOM
    // node, not a container that outlives the whole component.
    const tasks = renderTasks.current;

    (async () => {
      try {
        // Imported inside the effect, never at module scope: pdf.js touches
        // browser globals, and a "use client" module is still evaluated on the
        // server to produce the initial HTML.
        const pdfjs = await import("pdfjs-dist");
        pdfjs.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs";

        // Belt-and-suspenders alongside the cleanup below: cancel() releases
        // pdf.js's internal canvas-in-use marker SYNCHRONOUSLY, so doing this
        // before this pass's own render() calls run guarantees no canvas this
        // pass is about to draw into is still marked busy by a leftover task.
        for (const task of tasks.values()) task.cancel();
        tasks.clear();
        // Same belt-and-suspenders, for the loading task: this pass's own
        // cleanup (below) already destroys whatever the PREVIOUS pass left
        // behind before this pass's effect body ever runs, so this covers
        // it a second time rather than being the primary guarantee.
        // destroy() is async and can itself reject (its "Terminate" round
        // trip to a worker that may already be unreachable) -- swallowed,
        // because tearing down a stale pass is bookkeeping, never a
        // user-visible failure of the pass actually in flight.
        loadingTask.current?.destroy().catch(() => {});
        loadingTask.current = null;

        // pdf.js DETACHES the buffer it is given. Handing it `bytes` directly
        // would leave the caller holding a zero-length array, so every render
        // after the first would draw nothing.
        const task = pdfjs.getDocument({ data: bytes.slice() });
        loadingTask.current = task;
        const doc = await task.promise;
        if (cancelled || seq !== renderSeq.current) return;

        const dpr = window.devicePixelRatio || 1;
        const laid: RenderedPage[] = [];

        for (let n = 1; n <= doc.numPages; n++) {
          const page = await doc.getPage(n);
          if (cancelled || seq !== renderSeq.current) return;
          const natural = page.getViewport({ scale: 1 });
          const viewport = page.getViewport({ scale: RENDER_CSS_WIDTH / natural.width });
          laid.push({ pageNumber: n, width: natural.width, height: natural.height });
          // Commit page n's card SYNCHRONOUSLY, so its <canvas> is in
          // canvasRefs before the lookup below. This used to be a plain
          // setPages followed by waiting one animation frame, on the
          // assumption React would have committed by then. It often had
          // not: the lookup came back empty, the loop skipped the page, and
          // it stayed a blank white card for good -- a different set of
          // pages on every compile (measured: 2+4, then 2+4+6, then 4, then
          // 2 of a six-page PDF). Safe here: this runs after an await, never
          // inside React's own render or effect execution.
          //
          // Pages past n keep the PREVIOUS render's cards until this pass
          // reaches them, so a recompile redraws the preview in place rather
          // than collapsing it to one page and losing the scroll position.
          flushSync(() => setPages((prev) => [...laid, ...prev.slice(laid.length)]));

          const canvas = canvasRefs.current.get(n);
          const ctx = canvas?.getContext("2d");
          if (!canvas || !ctx) continue;

          // Backing store in device pixels for a sharp render. The CSS size is
          // NOT set here: the canvas fills its page card, whose aspect ratio
          // comes from the page itself, and the click and highlight maths
          // read the card's DISPLAYED size -- so neither the render width nor
          // the device pixel ratio ever leaks into a coordinate.
          canvas.width = Math.floor(viewport.width * dpr);
          canvas.height = Math.floor(viewport.height * dpr);
          ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

          const renderTask = page.render({ canvasContext: ctx, viewport });
          tasks.set(n, renderTask);
          try {
            await renderTask.promise;
          } finally {
            // Runs whether the render finished or was cancelled -- either
            // way this task is no longer in flight and must not be
            // cancel()'d again later (cancel() on an already-settled task is
            // harmless, but a stale Map entry would misreport this page as
            // still busy to the next pass's leftover-task sweep above).
            tasks.delete(n);
          }
        }
        if (!cancelled && seq === renderSeq.current) {
          // A shorter PDF than the last one: drop the previous render's
          // leftover cards past the new last page.
          setPages(laid);
          setError(null);
        }
      } catch (err) {
        if (cancelled || seq !== renderSeq.current) return;
        // Cancelling a RenderTask makes it REJECT its promise with a
        // RenderingCancelledException (verified against the installed
        // pdfjs-dist source) -- that is the expected, intentional result of
        // the cancel() calls above (a newer pass superseding this one, or
        // this effect tearing down), not a real failure. Routing it into
        // setError would blank the preview on every ordinary rapid zoom --
        // the exact bug this file exists to fix, just by a different path --
        // so it is deliberately swallowed here instead.
        if (err instanceof Error && err.name === "RenderingCancelledException") return;
        // Same idea for the loading task: reading the installed source,
        // "Loading aborted" and "Worker was destroyed" are the two messages
        // it raises for a task torn down before it finished loading -- both
        // reachable ONLY via destroy() (they gate on `task.destroyed` /
        // `worker.destroyed`, set nowhere else). Either one means this
        // pass's own sweep above, or its cleanup below, did the destroying
        // -- superseded or torn down, not a real failure to report.
        if (
          err instanceof Error &&
          (err.message === "Loading aborted" || err.message === "Worker was destroyed")
        ) {
          return;
        }
        setError(err instanceof Error ? err.message : String(err));
      }
    })();

    return () => {
      cancelled = true;
      // Release every canvas this pass still has marked busy -- on unmount
      // there is no next pass to do it via the sweep above, and on a
      // dependency change React runs this cleanup before the next pass's
      // effect body, so its own sweep finds nothing left to do.
      for (const task of tasks.values()) task.cancel();
      tasks.clear();
      // Mirrors the render-task cleanup immediately above, for the loading
      // task and its worker: nothing else will ever destroy() this pass's
      // task once this effect tears down or a new pass starts.
      loadingTask.current?.destroy().catch(() => {});
      loadingTask.current = null;
    };
  }, [bytes]);

  useEffect(() => {
    if (scrollToPage === null) return;
    const canvas = canvasRefs.current.get(scrollToPage);
    canvas?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [scrollToPage, highlight]);

  // With a PDF on screen, a compile in flight or a failed one leaves it there:
  // a broken edit must not blank the preview, and the previous render is
  // still the most useful thing available. These states are for the pane
  // that has nothing to show yet.
  if (!bytes) {
    if (status === "compiling") {
      return (
        <div className="flex h-full flex-1 flex-col items-center justify-center gap-2 bg-muted/20 text-muted-foreground">
          <Loader2 className="size-6 animate-spin" aria-hidden />
          <p className="text-[13px]">Compiling…</p>
        </div>
      );
    }
    if (status === "failed") {
      return (
        <div className="flex h-full flex-1 flex-col items-center justify-center gap-2 bg-muted/20 p-6 text-center text-muted-foreground">
          <FileWarning className="size-8" aria-hidden />
          <p className="text-[13px] font-medium text-foreground">No PDF to show</p>
          <p className="text-[12px]">The last compile failed. Fix the error and compile again.</p>
        </div>
      );
    }
    return (
      <div className="flex h-full flex-1 flex-col items-center justify-center gap-2 bg-muted/20 p-6 text-center text-muted-foreground">
        <p className="text-[13px]">Compile to see the PDF.</p>
      </div>
    );
  }

  return (
    <div className="relative flex h-full flex-1 flex-col items-center gap-3 overflow-y-auto bg-muted/20 p-4">
      {error && (
        // An OVERLAY, not a replacement for the canvas container below --
        // that container must stay mounted (so canvasRefs keeps every
        // page's <canvas>) even while an error is showing, or the render
        // pass that's meant to fix the error finds nowhere left to draw:
        // canvasRefs.get(n) comes back undefined for every page, the loop
        // `continue`s past all of them, and the preview stays blank until a
        // THIRD compile. Same visual result as before -- the error covers
        // the content -- reached without ever unmounting what's underneath.
        <div className="absolute inset-0 z-10 flex items-center justify-center bg-muted/20 p-6 text-center text-[13px] text-muted-foreground">
          The preview could not be displayed. {error}
        </div>
      )}
      {pages.map((page) => {
        const matched = highlight && highlight.page === page.pageNumber ? highlight : null;
        const pos = matched ? texToPercent({ x: matched.x, y: matched.y }, page) : null;
        return (
          <div
            key={page.pageNumber}
            // The card's aspect ratio is the PAGE's own, so the box has its
            // final size before pdf.js has drawn anything into it.
            style={{ aspectRatio: `${page.width} / ${page.height}` }}
            className={cn(
              "relative w-full shrink-0 overflow-hidden rounded-sm border bg-card shadow-sm",
              wide ? "max-w-3xl" : "max-w-md"
            )}
          >
            <canvas
              ref={(el) => {
                if (el) canvasRefs.current.set(page.pageNumber, el);
                else canvasRefs.current.delete(page.pageNumber);
              }}
              className="absolute inset-0 block h-full w-full bg-white"
              onDoubleClick={(e) => {
                const box = e.currentTarget.getBoundingClientRect();
                // The DISPLAYED scale -- CSS pixels on screen per TeX big
                // point -- read off the box at the moment of the click. The
                // canvas is drawn at one fixed width and scaled by CSS, so
                // this, not the render width, is what a click position is
                // measured in. getBoundingClientRect is CSS pixels, so no
                // DPR term enters.
                onPageDoubleClick(
                  page.pageNumber,
                  canvasToTex(
                    { x: e.clientX - box.left, y: e.clientY - box.top },
                    box.width / page.width
                  )
                );
              }}
            />
            {matched && pos && (
              <div
                className="pointer-events-none absolute animate-pulse rounded-sm bg-primary/30 ring-2 ring-primary"
                style={{
                  left: `${pos.x}%`,
                  top: `${pos.y}%`,
                  width: `max(4px, ${(matched.width / page.width) * 100}%)`,
                  height: `max(12px, ${(matched.height / page.height) * 100}%)`,
                }}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}
