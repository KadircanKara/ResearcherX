/**
 * What the retrieval scope line says, as a pure rule.
 *
 * The backend's `retrieving` event is the ONLY source here. Every branch below
 * corresponds to a decision the paper resolver / mention path actually made,
 * and none of them is inferred from anything else on screen:
 *
 *   scope_source "resolved"  the question's own words named papers and the
 *                            user clicked nothing — so the line quotes THEIR
 *                            phrase, not the paper title we matched, because
 *                            the phrase is the only thing they can change to
 *                            search wider.
 *   scope_source "mention"   the user picked the papers with `@`. They already
 *                            know which ones; the line only has to say whether
 *                            the search stayed inside them.
 *   scoped_count === 0       papers were named but none could be scoped to
 *                            (all deleted, or retrieval never ran). The
 *                            backend reports this `widened`; saying "scoped to
 *                            0 papers" would claim a scope that never applied.
 *   not scoped               global retrieval. `paper_count` is `len(scope)`
 *                            server-side — the papers actually searched — so
 *                            it is safe to state as a number.
 *
 * `widened` is True only when a chunk from OUTSIDE the named papers actually
 * landed in the context, so " and the rest of the library" is a statement
 * about what was read, not about what was attempted.
 *
 * This lives in `src/lib/` because vitest runs here in the node environment
 * with no jsdom: a rule left inside a component is a rule with no test.
 */

import { plural } from "./explorer";

/** The payload of the backend's `retrieving` SSE event, as the UI holds it. */
export interface RetrievingInfo {
  /** Papers actually searched. Becomes the whole project once widening fires. */
  paper_count: number;
  history_hits: number;
  scoped: boolean;
  /** How many papers were NAMED. Stays put when widening fires. */
  scoped_count: number;
  widened: boolean;
  /** Titles of named papers that returned no chunks at all. */
  empty_mentions: string[];
  scope_source: "mention" | "resolved";
  /** For a resolved scope, the phrases from the question that named them. */
  scope_evidence: string[];
}

export interface ScopeSegment {
  text: string;
  /** Rendered in the foreground colour — the user's own words. */
  emphasis?: boolean;
}

function papersSearched(count: number, widened: boolean): string {
  return `searching ${plural(count, "paper")}${
    widened ? " and the rest of the library." : " only."
  }`;
}

/**
 * The one-line scope statement for a turn in flight, or `null` when there is
 * nothing yet to say.
 *
 * Returned as segments rather than a string so the component can emphasise the
 * quoted phrases without this module knowing any markup.
 */
export function scopeLine(info: RetrievingInfo | null): ScopeSegment[] | null {
  if (!info) return null;

  // Named but unusable. Checked before the source branches: both of them
  // would otherwise render "searching 0 papers".
  if (info.scoped && info.scoped_count === 0) {
    return [{ text: "Mentions unavailable — searching the whole library." }];
  }

  if (info.scope_source === "resolved" && info.scoped_count > 0) {
    if (info.scope_evidence.length === 0) {
      return [
        {
          text: `Your question named ${plural(info.scoped_count, "paper")} — ${papersSearched(
            info.scoped_count,
            info.widened
          )}`,
        },
      ];
    }
    const segments: ScopeSegment[] = [{ text: "Matched " }];
    info.scope_evidence.forEach((phrase, i) => {
      if (i > 0) segments.push({ text: ", " });
      segments.push({ text: `“${phrase}”`, emphasis: true });
    });
    segments.push({
      text: ` in your question — ${papersSearched(info.scoped_count, info.widened)}`,
    });
    return segments;
  }

  if (info.scoped && info.scoped_count > 0) {
    return [
      {
        text: `You named ${plural(info.scoped_count, "paper")} — ${papersSearched(
          info.scoped_count,
          info.widened
        )}`,
      },
    ];
  }

  return [
    { text: `No paper named — searching all ${plural(info.paper_count, "paper")}.` },
  ];
}

/**
 * A paper the user NAMED that came back with nothing.
 *
 * Kept separate from the scope line and shown through streaming, not just
 * retrieval: the answer is being written from fewer papers than were asked
 * for, and that is exactly when the reader needs to know. Not persisted with
 * the message — the durable record is the answer itself, which the model is
 * instructed to qualify.
 */
export function emptyMentionsNote(info: RetrievingInfo | null): string | null {
  if (!info || info.empty_mentions.length === 0) return null;
  return `No excerpts from ${info.empty_mentions.join(", ")}.`;
}

export type ChatStatus = "idle" | "thinking" | "retrieving" | "streaming";

/**
 * The working line above the answer: what the turn is doing right now.
 * Returns `null` when there is nothing in flight.
 */
export function statusLabel(status: ChatStatus, info: RetrievingInfo | null): string | null {
  if (status === "idle") return null;
  if (status === "thinking") return "Reading the question";
  if (status === "retrieving") {
    return info ? `Searching ${plural(info.paper_count, "paper")}` : "Searching the library";
  }
  return "Writing the answer";
}
