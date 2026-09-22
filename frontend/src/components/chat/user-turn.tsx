import { Fragment } from "react";
import type { Paper } from "@/lib/types";

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * The question, as the user typed it.
 *
 * `content` is a historical record and is never rewritten, so the mention
 * highlight is a match against the CURRENT title of each mentioned id: after
 * a rename the text still holds the OLD title and the highlight simply stops
 * matching. That is the correct behaviour, not a bug to paper over — the
 * mention keeps working for retrieval SCOPE, which is id-based, and citation
 * chips DO follow a rename because the server re-labels them on read.
 */
export function UserTurn({
  content,
  mentions,
  papers,
}: {
  content: string;
  /** Paper ids the turn was scoped to. */
  mentions: string[];
  papers: Paper[];
}) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-primary px-4 py-2.5 text-sm leading-relaxed text-primary-foreground shadow-sm">
        <MentionedContent content={content} mentions={mentions} papers={papers} />
      </div>
    </div>
  );
}

function MentionedContent({
  content,
  mentions,
  papers,
}: {
  content: string;
  mentions: string[];
  papers: Paper[];
}) {
  if (mentions.length === 0) return <>{content}</>;
  const titles = mentions
    .map((id) => papers.find((p) => p.id === id)?.title)
    .filter((t): t is string => Boolean(t));
  if (titles.length === 0) return <>{content}</>;
  // Longest-first, same convention as reconcileMentions in lib/mentions.ts:
  // otherwise a shorter co-mentioned title that prefixes a longer one (e.g.
  // "RL" and "RL Survey") can steal the match and split the longer title in
  // two.
  const sortedTitles = [...titles].sort((a, b) => b.length - a.length);
  const parts = content.split(
    new RegExp(`(${sortedTitles.map(escapeRegExp).map((t) => `@${t}`).join("|")})`)
  );
  return (
    <>
      {parts.map((part, i) =>
        part.startsWith("@") && titles.some((t) => part === `@${t}`) ? (
          // Tinted from the bubble's OWN foreground: a tint mixed from the
          // page accent is blue-on-blue in here and renders invisible.
          <span key={i} className="rounded bg-primary-foreground/25 px-1 font-medium">
            {part}
          </span>
        ) : (
          <Fragment key={i}>{part}</Fragment>
        )
      )}
    </>
  );
}
