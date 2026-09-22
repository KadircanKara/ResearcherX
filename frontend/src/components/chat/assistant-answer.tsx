"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  CitationHoverCard,
  queryTermsFrom,
} from "@/components/chat/citation-hover-card";
import { citationMarks } from "@/lib/citation-marks";
import type { ChatCitation, ChatMessage } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * The answer's markdown scale.
 *
 * `@tailwindcss/typography` rather than a hand-written stylesheet: the plugin
 * is theme-aware through `--tw-prose-*` and `dark:prose-invert`, and the
 * citation hover card already reads from it — two stylesheets arguing over
 * every element's font-size is exactly what the old chat.css produced.
 *
 * A wide table is contained by CSS alone (`display:block; overflow-x:auto` on
 * the table itself), not by a wrapper component: a wrapper would mean a
 * `components` override whose only job is to drop react-markdown's `node`
 * prop.
 */
const ANSWER_PROSE =
  "prose prose-sm dark:prose-invert max-w-none text-[14px] leading-relaxed " +
  "prose-p:my-2 prose-li:my-0.5 prose-ul:my-2 prose-ol:my-2 " +
  "prose-headings:mt-4 prose-headings:mb-1.5 prose-headings:text-[15px] " +
  "prose-pre:text-[12px] prose-code:text-[12px] " +
  "[&_table]:block [&_table]:overflow-x-auto";

/** The writing caret, on the END of the last block rather than a line of its own. */
const LIVE_CARET =
  "[&>:last-child]:after:ml-1 [&>:last-child]:after:inline-block [&>:last-child]:after:h-3.5 " +
  "[&>:last-child]:after:w-[2px] [&>:last-child]:after:translate-y-0.5 " +
  "[&>:last-child]:after:animate-pulse [&>:last-child]:after:bg-foreground/70 " +
  "[&>:last-child]:after:align-baseline [&>:last-child]:after:content-['']";

/**
 * A finished answer: its markdown, its `[n]` markers turned into hoverable
 * citations, and the sources it used.
 *
 * The markers are the app's own `[n]` convention, resolved by
 * `lib/citation-marks.ts` against the citations this message actually
 * carries — an unknown number is left as prose rather than dressed up as a
 * source that does not exist.
 */
export function AssistantAnswer({
  message,
  question,
  projectId,
}: {
  message: ChatMessage;
  /** The question this answered, for highlighting terms in the hover card. */
  question: string;
  projectId: string;
}) {
  const queryTerms = queryTermsFrom(question);

  return (
    <div className="max-w-[92%] space-y-3">
      <div className={ANSWER_PROSE}>
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          // Tuple form, not citationMarks({...}): unified treats a bare
          // function as an ATTACHER and calls it with the options, using its
          // return value as the transformer. Passing an already-invoked
          // transformer makes unified call it again with no arguments, and it
          // crashes on an undefined tree — after passing tsc, lint and build,
          // so only a browser check finds it.
          rehypePlugins={[
            [citationMarks, { valid: new Set(message.citations.map((c) => c.n)) }],
          ]}
          components={{
            span: ({ node, children, ...props }) => {
              // react-markdown's hast node. Named only so it cannot be spread
              // onto the DOM, where React would warn about an unknown prop.
              void node;
              const raw = props as Record<string, string | undefined>;
              const n = Number(raw["data-citation-n"]);
              const groupAttr = raw["data-citation-group"];
              if (!groupAttr || Number.isNaN(n)) return <span {...props}>{children}</span>;
              const group = groupAttr
                .split(",")
                .map(Number)
                .map((num) => message.citations.find((c) => c.n === num))
                .filter((c): c is ChatCitation => c !== undefined);
              const start = group.findIndex((c) => c.n === n);
              if (start === -1) return <span {...props}>{children}</span>;
              return (
                <CitationHoverCard
                  citations={group}
                  startIndex={start}
                  projectId={projectId}
                  queryTerms={queryTerms}
                  variant="inline"
                />
              );
            },
          }}
        >
          {message.content}
        </ReactMarkdown>
      </div>

      {message.citations.length > 0 && (
        <div className="space-y-1.5 border-t pt-2 text-xs text-muted-foreground">
          <p className="font-medium text-foreground">Sources</p>
          <ul className="space-y-1">
            {message.citations.map((citation, i) => (
              <li key={citation.n} className="flex items-baseline gap-1.5">
                <CitationHoverCard
                  citations={message.citations}
                  startIndex={i}
                  projectId={projectId}
                  queryTerms={queryTerms}
                  variant="chip"
                />
                <span className="min-w-0 flex-1 truncate">{citation.title}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/**
 * The answer as it arrives.
 *
 * No citation plugin here: citations arrive with the `done` event, so
 * mid-stream there is nothing to resolve a marker against and a `[7]` in the
 * text is still just text.
 */
export function StreamingAnswer({ text }: { text: string }) {
  return (
    <div className={cn("max-w-[92%]", ANSWER_PROSE, LIVE_CARET)}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
}
