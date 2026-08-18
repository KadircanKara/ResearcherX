import type { Paper } from "./types";

/** A paper the user picked from the "@" dropdown. The id is the truth; the
 *  title is only how the mention is spelled in the text. */
export type Mention = { paperId: string; title: string };

/** Longest a mention query can get before we stop looking — a filter, not a
 *  sentence. */
const MAX_QUERY = 80;

/**
 * The "@query" being typed at the caret, or null.
 *
 * "@" only triggers at the start of a word, so an email address never opens
 * the dropdown, and whitespace ends the query.
 */
export function findMentionQuery(
  text: string,
  caret: number
): { query: string; start: number } | null {
  for (let i = caret - 1; i >= 0 && caret - i <= MAX_QUERY; i--) {
    const ch = text[i];
    if (ch === "@") {
      const before = i === 0 ? "" : text[i - 1];
      if (before && !/\s/.test(before)) return null;
      return { query: text.slice(i + 1, caret), start: i };
    }
    if (/\s/.test(ch)) return null;
  }
  return null;
}

/** Replace the in-progress "@query" with the full title. Never truncated: the
 *  prompt reads what the user sees. */
export function insertMention(
  text: string,
  start: number,
  caret: number,
  title: string
): { text: string; caret: number } {
  const next = `${text.slice(0, start)}@${title} ${text.slice(caret)}`;
  return { text: next, caret: start + title.length + 2 };
}

/**
 * Drop mentions whose text no longer stands.
 *
 * Pure and offset-free by design: offsets drift on every edit and rot
 * silently, so a mention is instead re-derived from the text on each change.
 * Two papers sharing a title are matched by occurrence COUNT, so both survive
 * only while both occurrences do.
 */
export function reconcileMentions(text: string, mentions: Mention[]): Mention[] {
  const seen = new Map<string, number>();
  const kept: Mention[] = [];
  for (const mention of mentions) {
    const needle = `@${mention.title}`;
    const used = seen.get(needle) ?? 0;
    if (countOccurrences(text, needle) > used) {
      seen.set(needle, used + 1);
      kept.push(mention);
    }
  }
  return kept;
}

function countOccurrences(haystack: string, needle: string): number {
  let count = 0;
  let from = 0;
  for (;;) {
    const at = haystack.indexOf(needle, from);
    if (at === -1) return count;
    count++;
    from = at + needle.length;
  }
}

/** Papers whose title contains `query`, earliest match first. */
export function matchPapers(papers: Paper[], query: string, limit = 8): Paper[] {
  const q = query.trim().toLowerCase();
  const scored = papers
    .map((p) => ({ p, at: q ? p.title.toLowerCase().indexOf(q) : 0 }))
    .filter(({ at }) => at !== -1)
    .sort((a, b) => a.at - b.at || a.p.title.localeCompare(b.p.title));
  return scored.slice(0, limit).map(({ p }) => p);
}
