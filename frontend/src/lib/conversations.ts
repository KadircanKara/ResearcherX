/**
 * Pure rules for the Chat conversation list and thread header.
 *
 * Unlike Explorer's, the stamps here are REAL and come from the backend as UTC
 * ISO-8601 (`2026-08-19T14:02:11.123456+00:00`). They are converted to the
 * reader's own local wall clock exactly once, in `toLocalStamp`, and every rule
 * downstream formats the `YYYY-MM-DDTHH:MM` string that produces — so a
 * conversation started late in the evening is dated the day the reader saw it,
 * not the day it was in UTC.
 *
 * There is no hydration hazard in doing this: both Chat screens are client
 * components that render a skeleton until their fetch lands, so no timestamp
 * is ever part of the server-rendered HTML.
 *
 * The wording is the app prototype's: the list reads `18 Sep 2026` for when a
 * conversation started and `18 Sep` for its last activity, and the thread
 * header reads `Started 18 Sep 2026`. Explorer owns different wording for its
 * own stamps; `lib/format.ts` keeps only the substrate both compose.
 */

import { formatDate, formatShortDay, plural } from "./format";
import type { ChatMessage } from "./types";

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

/** A `Date` as the local-wall-clock `YYYY-MM-DDTHH:MM` string the formatters
 *  in `lib/format.ts` are written against. */
export function toLocalStamp(date: Date): string {
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

function localStamp(iso: string): string | null {
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? null : toLocalStamp(at);
}

/**
 * The Started column: `18 Sep 2026`.
 * An unparseable stamp yields `""` rather than `Invalid Date` — a broken cell
 * must not be louder than a correct one.
 */
export function startedDay(iso: string): string {
  const stamp = localStamp(iso);
  return stamp ? formatDate(stamp) : "";
}

/** The Last activity column: `18 Sep`, the day without its year. */
export function activityDay(iso: string): string {
  const stamp = localStamp(iso);
  return stamp ? formatShortDay(stamp) : "";
}

/** Thread header: `Started 18 Sep 2026`, or `""` for a broken stamp. */
export function startedAt(iso: string): string {
  const day = startedDay(iso);
  return day ? `Started ${day}` : "";
}

/** List header count: `0 conversations`, `1 conversation`, `5 conversations`. */
export function conversationCount(total: number): string {
  return plural(total, "conversation");
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

/** The parts of a click that decide what a conversation row does with it. */
export interface RowClick {
  /** The list is in bulk-select (edit) mode. */
  editing: boolean;
  /** `MouseEvent.button`: 0 is the primary button. */
  button: number;
  metaKey: boolean;
  ctrlKey: boolean;
  shiftKey: boolean;
  altKey: boolean;
}

/**
 * What a click anywhere on a conversation row does.
 *
 * The whole row is one real link (the title's, stretched over the row), so
 * outside edit mode every click is left to the browser: a plain click opens
 * the conversation, and cmd/ctrl/shift/middle-click open it in a new tab or
 * window exactly as they would on any link.
 *
 * In edit mode a PLAIN primary click selects the row instead — the list is
 * being picked from, not read. A modified click still navigates: it can only
 * mean "open this somewhere else", and swallowing it would make the row the
 * one link on the page that ignores cmd-click.
 */
export function rowClickAction(click: RowClick): "toggle" | "navigate" {
  if (!click.editing) return "navigate";
  if (click.button !== 0) return "navigate";
  if (click.metaKey || click.ctrlKey || click.shiftKey || click.altKey) return "navigate";
  return "toggle";
}

/**
 * One question and the answer it got.
 *
 * The grouping is deliberately forgiving of shapes the backend does not
 * currently produce, because the alternative is dropping a message on the
 * floor: an assistant message with no question before it opens a turn of its
 * own, and a question that somehow got two answers keeps both. The thread
 * uses it to hand each answer the question it answered (for highlighting that
 * question's terms in the citation card).
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
