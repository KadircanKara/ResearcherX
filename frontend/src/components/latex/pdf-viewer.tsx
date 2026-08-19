"use client";

import { useEffect, useRef, useState } from "react";
import { canvasToTex, type TexPoint } from "@/lib/latex-sync";

export interface PdfHighlight {
  page: number;
  x: number;
  y: number;
  width: number;
  height: number;
}

interface PdfViewerProps {
  bytes: Uint8Array | null;
  scale: number;
  highlight: PdfHighlight | null;
  scrollToPage: number | null;
  onPageDoubleClick: (page: number, point: TexPoint) => void;
}

interface RenderedPage {
  pageNumber: number;
  /** CSS pixels, already multiplied by `scale`. */
  width: number;
  height: number;
}

export function PdfViewer({
  bytes,
  scale,
  highlight,
  scrollToPage,
  onPageDoubleClick,
}: PdfViewerProps) {
  const [pages, setPages] = useState<RenderedPage[]>([]);
  const [error, setError] = useState<string | null>(null);
  const canvasRefs = useRef<Map<number, HTMLCanvasElement>>(new Map());
  const containerRef = useRef<HTMLDivElement>(null);
  // Bumped on every render pass so a slower earlier PDF cannot paint over a
  // faster later one -- the same out-of-order guard papers/page.tsx uses.
  const renderSeq = useRef(0);

  useEffect(() => {
    if (!bytes) {
      setPages([]);
      return;
    }
    const seq = ++renderSeq.current;
    let cancelled = false;

    (async () => {
      try {
        // Imported inside the effect, never at module scope: pdf.js touches
        // browser globals, and a "use client" module is still evaluated on the
        // server to produce the initial HTML.
        const pdfjs = await import("pdfjs-dist");
        pdfjs.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs";

        // pdf.js DETACHES the buffer it is given. Handing it `bytes` directly
        // would leave the caller holding a zero-length array, so every render
        // after the first would draw nothing.
        const doc = await pdfjs.getDocument({ data: bytes.slice() }).promise;
        if (cancelled || seq !== renderSeq.current) return;

        const dpr = window.devicePixelRatio || 1;
        const laid: RenderedPage[] = [];

        for (let n = 1; n <= doc.numPages; n++) {
          const page = await doc.getPage(n);
          if (cancelled || seq !== renderSeq.current) return;
          const viewport = page.getViewport({ scale });
          laid.push({ pageNumber: n, width: viewport.width, height: viewport.height });
          setPages([...laid]);

          // The canvas for page n only exists after React has flushed the
          // element for it, so wait a frame before drawing into it.
          await new Promise((r) => requestAnimationFrame(() => r(null)));
          if (cancelled || seq !== renderSeq.current) return;

          const canvas = canvasRefs.current.get(n);
          const ctx = canvas?.getContext("2d");
          if (!canvas || !ctx) continue;

          // Backing store in device pixels for a sharp render; CSS size stays
          // in the scale-multiplied units the click maths uses, so device
          // pixel ratio never leaks into a coordinate.
          canvas.width = Math.floor(viewport.width * dpr);
          canvas.height = Math.floor(viewport.height * dpr);
          canvas.style.width = `${viewport.width}px`;
          canvas.style.height = `${viewport.height}px`;
          ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
          await page.render({ canvasContext: ctx, viewport }).promise;
        }
        if (!cancelled && seq === renderSeq.current) setError(null);
      } catch (err) {
        if (cancelled || seq !== renderSeq.current) return;
        setError(err instanceof Error ? err.message : String(err));
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [bytes, scale]);

  useEffect(() => {
    if (scrollToPage === null) return;
    const canvas = canvasRefs.current.get(scrollToPage);
    canvas?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [scrollToPage, highlight]);

  if (error) {
    return (
      <div className="flex h-full items-center justify-center p-6 text-center text-sm text-muted-foreground">
        The preview could not be displayed. {error}
      </div>
    );
  }

  if (!bytes) {
    return (
      <div className="flex h-full items-center justify-center p-6 text-center text-sm text-muted-foreground">
        Compile to see a preview.
      </div>
    );
  }

  return (
    <div ref={containerRef} className="h-full overflow-auto bg-muted/40 p-4">
      <div className="flex flex-col items-center gap-4">
        {pages.map((page) => (
          <div key={page.pageNumber} className="relative shadow-sm">
            <canvas
              ref={(el) => {
                if (el) canvasRefs.current.set(page.pageNumber, el);
                else canvasRefs.current.delete(page.pageNumber);
              }}
              className="block bg-white"
              onDoubleClick={(e) => {
                const box = e.currentTarget.getBoundingClientRect();
                // getBoundingClientRect is CSS pixels, which is exactly the
                // unit the canvas was sized in, so dividing by `scale` lands
                // back in TeX big points with no DPR term.
                onPageDoubleClick(
                  page.pageNumber,
                  canvasToTex(
                    { x: e.clientX - box.left, y: e.clientY - box.top },
                    scale
                  )
                );
              }}
            />
            {highlight && highlight.page === page.pageNumber && (
              <div
                className="pointer-events-none absolute animate-pulse rounded-sm bg-primary/30 ring-2 ring-primary"
                style={{
                  left: highlight.x * scale,
                  top: highlight.y * scale,
                  width: Math.max(highlight.width * scale, 4),
                  height: Math.max(highlight.height * scale, 12),
                }}
              />
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
