"use client";

import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { CitationChip } from "@/components/chat/citation-chip";
import { citationMarks } from "@/lib/citation-marks";
import type { ChatCitation } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * Every element an answer can contain, with the app prototype's classes.
 *
 * The prototype hand-parses a small markdown subset (paragraphs, lists,
 * tables, bold, italic, inline code). Real answers are model output and can
 * carry anything markdown can, so this stays on react-markdown — which also
 * escapes raw HTML, the XSS policy for LLM-derived text — and maps the
 * prototype's elements to its exact classes. Headings, code blocks, quotes
 * and links have no prototype counterpart and get the plainest styling that
 * sits with the rest.
 */
const ELEMENTS: Components = {
  p: ({ node, className, ...props }) => {
    void node;
    return <p className={cn("leading-relaxed", className)} {...props} />;
  },
  ul: ({ node, className, ...props }) => {
    void node;
    return <ul className={cn("list-disc space-y-1 pl-5", className)} {...props} />;
  },
  ol: ({ node, className, ...props }) => {
    void node;
    return <ol className={cn("list-decimal space-y-1 pl-5", className)} {...props} />;
  },
  li: ({ node, className, ...props }) => {
    void node;
    return <li className={cn("leading-relaxed", className)} {...props} />;
  },
  table: ({ node, className, ...props }) => {
    void node;
    return (
      <div className="overflow-x-auto">
        <table className={cn("w-full border-collapse text-sm", className)} {...props} />
      </div>
    );
  },
  tbody: ({ node, className, ...props }) => {
    void node;
    return <tbody className={cn("[&>tr:last-child]:border-0", className)} {...props} />;
  },
  tr: ({ node, className, ...props }) => {
    void node;
    return <tr className={cn("border-b", className)} {...props} />;
  },
  th: ({ node, className, ...props }) => {
    void node;
    return <th className={cn("px-2 py-1.5 text-left font-medium", className)} {...props} />;
  },
  td: ({ node, className, ...props }) => {
    void node;
    return <td className={cn("px-2 py-1.5 align-top", className)} {...props} />;
  },
  code: ({ node, className, ...props }) => {
    void node;
    return (
      <code
        className={cn("rounded bg-muted px-1 py-0.5 font-mono text-[0.85em]", className)}
        {...props}
      />
    );
  },
  pre: ({ node, className, ...props }) => {
    void node;
    return (
      <pre
        className={cn(
          "overflow-x-auto rounded-md bg-muted p-3 font-mono text-[0.85em] [&>code]:bg-transparent [&>code]:p-0 [&>code]:text-[1em]",
          className
        )}
        {...props}
      />
    );
  },
  h1: ({ node, className, ...props }) => {
    void node;
    return <h1 className={cn("text-base font-semibold", className)} {...props} />;
  },
  h2: ({ node, className, ...props }) => {
    void node;
    return <h2 className={cn("text-base font-semibold", className)} {...props} />;
  },
  h3: ({ node, className, ...props }) => {
    void node;
    return <h3 className={cn("font-semibold", className)} {...props} />;
  },
  h4: ({ node, className, ...props }) => {
    void node;
    return <h4 className={cn("font-semibold", className)} {...props} />;
  },
  blockquote: ({ node, className, ...props }) => {
    void node;
    return (
      <blockquote className={cn("border-l-2 pl-3 text-muted-foreground", className)} {...props} />
    );
  },
  a: ({ node, className, ...props }) => {
    void node;
    return (
      <a
        {...props}
        className={cn("underline underline-offset-2", className)}
        target="_blank"
        rel="noopener noreferrer"
      />
    );
  },
};

/**
 * An answer's markdown, with its `[n]` markers turned into citation chips.
 *
 * The markers are the app's own `[n]` convention, resolved by
 * `lib/citation-marks.ts` against the citations this message actually
 * carries — an unknown number is left as prose rather than dressed up as a
 * source that does not exist. Mid-stream there are no citations yet (they
 * arrive with `done`), so a `[7]` in a streaming answer is still just text.
 */
export function MarkdownRenderer({
  markdown,
  citations,
  projectId,
  queryTerms,
}: {
  markdown: string;
  citations: ChatCitation[];
  projectId: string;
  queryTerms: string[];
}) {
  const components: Components =
    citations.length === 0
      ? ELEMENTS
      : {
          ...ELEMENTS,
          span: ({ node, children, ...props }) => {
            void node;
            const n = Number((props as Record<string, unknown>)["data-citation-n"]);
            const citation = Number.isNaN(n) ? undefined : citations.find((c) => c.n === n);
            if (!citation) return <span {...props}>{children}</span>;
            return (
              <CitationChip citation={citation} projectId={projectId} queryTerms={queryTerms} />
            );
          },
        };

  return (
    <div className="space-y-3 text-sm text-foreground">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        // Tuple form, not citationMarks({...}): unified treats a bare
        // function as an ATTACHER and calls it with the options, using its
        // return value as the transformer. Passing an already-invoked
        // transformer makes unified call it again with no arguments, and it
        // crashes on an undefined tree — after passing tsc, lint and build,
        // so only a browser check finds it.
        rehypePlugins={
          citations.length === 0
            ? []
            : [[citationMarks, { valid: new Set(citations.map((c) => c.n)) }]]
        }
        components={components}
      >
        {markdown}
      </ReactMarkdown>
    </div>
  );
}
