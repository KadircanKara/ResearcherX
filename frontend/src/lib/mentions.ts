import type { Paper } from "./types";

/** A paper the user picked from the "@" dropdown. The id is the truth; the
 *  title is only how the mention is spelled in the text. */
export type Mention = { paperId: string; title: string };

/** Longest a mention query can get before we stop looking — a filter, not a
 *  sentence. */
const MAX_QUERY = 80;

/**
 * How many papers one message may be scoped to.
 *
 * MUST match `ChatRequest.mentioned_paper_ids`'s `max_length` in
 * `backend/app/schemas/chat.py`. The server is the real enforcement — this
 * copy exists so an 11th pick is refused with a visible message instead of
 * being accepted, sent, and rejected as a 422 after the composer has already
 * been cleared.
 */
export const MAX_MENTIONS = 10;

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
  const after = text.slice(caret);
  const space = after.length > 0 && /\s/.test(after[0]) ? '' : ' ';
  const next = `${text.slice(0, start)}@${title}${space}${after}`;
  return { text: next, caret: start + 1 + title.length + 1 };
}

/**
 * Drop mentions whose text no longer stands.
 *
 * Pure and offset-free by design: offsets drift on every edit and rot
 * silently, so a mention is instead re-derived from the text on each change.
 * Two papers sharing a title are matched by occurrence COUNT, so both survive
 * only while both occurrences do.
 *
 * When two mentions have overlapping titles (e.g., "Search" and "Search Methods"),
 * the longer one matches first and claims its span, preventing the shorter one
 * from matching within the same text region. Two papers with identical titles
 * are still matched by count — both need separate occurrences to survive.
 */
export function reconcileMentions(text: string, mentions: Mention[]): Mention[] {
  const spans = claimSpans(text, mentions);
  // Return mentions in original order
  return mentions.filter((_, i) => spans.has(i));
}

/**
 * The `@title` span each mention stands on, keyed by its index in `mentions`.
 * A mention with no free occurrence has no entry. This is `reconcileMentions`'
 * matching rule, shared so that removing a mention deletes exactly the span
 * reconcile would have credited to it.
 */
function claimSpans(text: string, mentions: Mention[]): Map<number, [number, number]> {
  // Track which character spans have been claimed by matches
  const consumed: Array<[start: number, end: number]> = [];

  // Create array of (mention, originalIndex) and sort by descending title length
  const indexed = mentions.map((m, i) => ({ mention: m, index: i }));
  indexed.sort((a, b) => b.mention.title.length - a.mention.title.length);

  const claimed = new Map<number, [number, number]>();

  for (const { mention, index } of indexed) {
    const needle = `@${mention.title}`;
    let searchFrom = 0;

    for (;;) {
      const at = text.indexOf(needle, searchFrom);
      if (at === -1) break;

      const end = at + needle.length;
      const overlaps = consumed.some(([cStart, cEnd]) => at < cEnd && end > cStart);

      if (!overlaps) {
        consumed.push([at, end]);
        claimed.set(index, [at, end]);
        break;
      }

      searchFrom = at + 1;
    }
  }

  return claimed;
}

/**
 * The text with one mention taken out — what the chip's remove button does.
 *
 * The chip only mirrors a title that is IN the text, so removing it has to
 * delete that `@title` from the text: the text is what gets sent and what the
 * user bubble shows, and a mention the text still spells out would simply be
 * re-derived on the next keystroke. The span removed is the one
 * `reconcileMentions` credits to this mention, so a shorter title sitting
 * inside a longer one ("@Search" in "@Search Methods") is never cut out of
 * the longer one. One adjoining space goes with it, so no double space is
 * left behind. `index` rather than a paper id, because two papers can share a
 * title and the chip removed is one specific mention.
 */
export function removeMention(text: string, mentions: Mention[], index: number): string {
  const span = claimSpans(text, mentions).get(index);
  if (!span) return text;
  let [start, end] = span;
  if (/\s/.test(text[end] ?? "")) end += 1;
  else if (start > 0 && /\s/.test(text[start - 1])) start -= 1;
  return text.slice(0, start) + text.slice(end);
}

/** The mention list with `paper` added, unless it is already there — the same
 *  paper picked twice is one scope entry, however often the text names it. */
export function withMention(mentions: Mention[], paper: Pick<Paper, "id" | "title">): Mention[] {
  if (mentions.some((m) => m.paperId === paper.id)) return mentions;
  return [...mentions, { paperId: paper.id, title: paper.title }];
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
