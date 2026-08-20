/**
 * Pure formatting rules for the Chat conversation list and thread header.
 *
 * Unlike `lib/explorer.ts`, the stamps here are REAL and come from the backend
 * as UTC ISO-8601 (`2026-08-19T14:02:11.123456+00:00`). They are converted to
 * the reader's own local wall clock exactly once, in `toLocalStamp`, and every
 * rule downstream operates on the `YYYY-MM-DDTHH:MM` string that produces — so
 * "Today" means today where the reader is sitting, and the day-boundary logic
 * in `formatActivity` never has to know about time zones.
 *
 * There is no hydration hazard in doing this: both Chat screens are client
 * components that render a skeleton until their fetch lands, so no timestamp
 * is ever part of the server-rendered HTML.
 */

import { formatActivity, plural, startedLabel } from "./explorer";
import type { ChatMessage } from "./types";

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

/** A `Date` as the local-wall-clock `YYYY-MM-DDTHH:MM` string the explorer
 *  date rules are written against. */
export function toLocalStamp(date: Date): string {
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

/**
 * Last-activity column: `Today, 14:02`, `Yesterday, 09:41`, `16 Aug 2026`.
 * An unparseable stamp yields `""` rather than `Invalid Date` — a broken cell
 * must not be louder than a correct one.
 */
export function activityLabel(iso: string, now: Date = new Date()): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  return formatActivity(toLocalStamp(at), toLocalStamp(now));
}

/** Thread header: `started today` / `started yesterday` / `started 2 Aug 2026`. */
export function startedAt(iso: string, now: Date = new Date()): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  return `started ${startedLabel(toLocalStamp(at), toLocalStamp(now))}`;
}

/** List header count. Zero is spelled out — "0 conversations" reads as a bug. */
export function conversationCount(total: number): string {
  return total === 0 ? "No conversations yet" : plural(total, "conversation");
}

/**
 * Thread header length. Counts USER turns, not messages: a "question" is what
 * the reader asked, and pairing it with the answer would double every figure.
 * `alsoSent` is for turns asked since the snapshot was fetched. The header is
 * rendered from the conversation the page loaded, while the turns themselves
 * live in `ChatStream`'s own state — without this the count would freeze at
 * whatever it was when the thread was opened and quietly contradict the turns
 * on screen. It is not an estimate: it is the number of sends this view made.
 */
export function questionCount(
  messages: readonly ChatMessage[],
  alsoSent = 0
): string {
  return plural(
    messages.filter((m) => m.role === "user").length + alsoSent,
    "question"
  );
}

/**
 * The Started column: `Today`, `Yesterday`, `16 Aug 2026`.
 *
 * Date only, never a clock — unlike Last activity. When a thread began is a
 * day; the minute it began is not something anyone returns to a list to read.
 */
export function startedDay(iso: string, now: Date = new Date()): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  // Slicing the time off is what makes `formatActivity` drop the clock.
  return formatActivity(toLocalStamp(at).slice(0, 10), toLocalStamp(now));
}

/**
 * One question and the answer it got.
 *
 * The reading-column treatment draws a hairline BETWEEN turns, which means the
 * renderer has to know where a turn ends — a flat message list cannot say. The
 * grouping is deliberately forgiving of shapes the backend does not currently
 * produce, because the alternative is dropping a message on the floor: an
 * assistant message with no question before it opens a turn of its own, and a
 * question that somehow got two answers keeps both.
 */
export interface ChatTurn {
  /** Stable across re-renders: the id of the first message in the turn. */
  key: string;
  question: ChatMessage | null;
  answers: ChatMessage[];
}

export function groupTurns(messages: readonly ChatMessage[]): ChatTurn[] {
  const turns: ChatTurn[] = [];
  for (const message of messages) {
    if (message.role === "user" || turns.length === 0) {
      turns.push({
        key: message.id,
        question: message.role === "user" ? message : null,
        answers: message.role === "user" ? [] : [message],
      });
      continue;
    }
    turns[turns.length - 1].answers.push(message);
  }
  return turns;
}
