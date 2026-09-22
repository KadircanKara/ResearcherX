/**
 * Pure rules for the Explorer screen.
 *
 * Everything here is a rule the design states, so it lives outside the
 * components and is tested: this repo runs vitest in the NODE environment with
 * no jsdom, so logic left inside a component is logic that is never tested.
 *
 * Dates are handled as plain `YYYY-MM-DD[THH:MM]` strings and never through
 * `new Date(...)` local parsing: Explorer is server-rendered and then hydrated,
 * and a server in one timezone disagreeing with a browser in another is a
 * hydration mismatch that only shows up on someone else's machine. The stamp
 * primitives come from `lib/format.ts`; the WORDING below is Explorer's own,
 * and deliberately differs from Chat's (see the note in `lib/conversations.ts`).
 */
import type { ExplorationTurn, ReviewIssue } from "./explorer-data";
import { MONTHS, datePart, previousDay, plural, timePart } from "./format";

/* ── Distance to the library ─────────────────────────────────────────── */

/**
 * The bands the meter labels. Named constants rather than literals inside a
 * chain of ternaries because the numbers are the design's decision, and the
 * card, the meter and any future filter must read the same ones.
 *
 * `close` is exclusive at 0.4 and `adjacent` inclusive at 0.7, matching the
 * prototype: 0.4 exactly is already "adjacent", 0.7 exactly is not yet "far".
 */
export const DISTANCE_CLOSE = 0.4;
export const DISTANCE_ADJACENT = 0.7;

export type DistanceLabel =
  | "in library"
  | "close to your library"
  | "adjacent"
  | "far";

/**
 * A paper already in the library has NO distance — not a zero. Zero is a real
 * distance and would read as "identical to something you have", so the absent
 * case gets its own label rather than falling through the bands.
 */
export function distanceLabel(distance: number | undefined): DistanceLabel {
  if (distance === undefined) return "in library";
  if (distance < DISTANCE_CLOSE) return "close to your library";
  if (distance <= DISTANCE_ADJACENT) return "adjacent";
  return "far";
}

/** The meter reads as closeness, so it is the inverse of distance. A paper
 *  already in the library fills it completely. Clamped: a distance outside
 *  [0,1] would otherwise render a bar wider than its track. */
export function distanceMeterPercent(distance: number | undefined): number {
  if (distance === undefined) return 100;
  return Math.max(0, Math.min(100, (1 - distance) * 100));
}

/** `0.31`, or an em dash for a paper already in the library. */
export function formatDistance(distance: number | undefined): string {
  return distance === undefined ? "—" : distance.toFixed(2);
}

/* ── The process trail's one-line summary ────────────────────────────── */

/** `pass`, or `2 issues` — the critic's verdict in the words the trail uses. */
export function reviewLabel(review: ExplorationTurn["review"]): string {
  if (review === "pass") return "pass";
  return `${review.length} ${review.length === 1 ? "issue" : "issues"}`;
}

/**
 * The collapsed trail's whole content: what the run planned, what it ran, and
 * how the review landed. Planned and searched are separate counts because a
 * retried query is still one plan entry — they only coincide in this mock.
 */
export function trailSummary(turn: {
  queries: unknown[];
  review: ExplorationTurn["review"];
}): string {
  return `Planned ${turn.queries.length} queries · searched ${turn.queries.length} · reviewed: ${reviewLabel(turn.review)}`;
}

/** The colour a review issue's dot takes. `low` is deliberately unremarkable:
 *  a note worth reading is not a warning. */
export function severityClass(severity: ReviewIssue["severity"]): string {
  if (severity === "high") return "text-destructive";
  if (severity === "medium") return "text-warning";
  return "text-muted-foreground";
}

/* ── Replay ──────────────────────────────────────────────────────────── */

/**
 * Replay re-runs one finished turn as if it were happening now. The stages are
 * the pipeline's own: plan, then one per search, then write, then review.
 */
export const REPLAY_STAGE_PLANNING = 0;
/** Everything before this is still in flight; at this stage the turn is done. */
export const REPLAY_STAGE_DONE = 6;

/** When each stage lands, in ms from the start. Index i sets stage i + 1, so
 *  the run opens on stage 0 (planning) and the last entry starts the answer. */
export const REPLAY_STAGE_DELAYS_MS = [1000, 2200, 3400, 4600, 5700] as const;
/** The answer starts streaming as the last stage lands. */
export const REPLAY_STREAM_START_MS = 5700;
/** Per word of the answer. */
export const REPLAY_WORD_MS = 28;
/** The shortest a replay may run, so a short answer does not snap shut. */
export const REPLAY_MIN_DURATION_MS = 7900;
/** Breath between the last word and the trail closing. */
export const REPLAY_TAIL_MS = 200;

/** One scheduled change: at `at` ms, move to `stage` and/or show `text`. */
export type ReplayFrame = {
  at: number;
  stage?: number;
  /** The answer as far as it has streamed. */
  text?: string;
};

/**
 * The whole replay as DATA rather than a pile of inline `setTimeout` calls:
 * the component walks this list and schedules one timer per frame, so the
 * timing is testable and the component holds nothing but the timer handles.
 *
 * The end is `REPLAY_MIN_DURATION_MS` unless the answer is long enough to
 * still be streaming then — the prototype's fixed 7.9s would have cut off any
 * answer over ~78 words, which its own sample happened to stay under.
 */
export function replaySchedule(answer: string): ReplayFrame[] {
  const words = answer.split(" ").filter(Boolean);
  const frames: ReplayFrame[] = REPLAY_STAGE_DELAYS_MS.map((at, index) => ({
    at,
    stage: index + 1,
  }));

  let lastWordAt = REPLAY_STREAM_START_MS;
  words.forEach((_, index) => {
    lastWordAt = REPLAY_STREAM_START_MS + index * REPLAY_WORD_MS;
    frames.push({ at: lastWordAt, text: words.slice(0, index + 1).join(" ") });
  });

  frames.push({
    at: Math.max(REPLAY_MIN_DURATION_MS, lastWordAt + REPLAY_TAIL_MS),
    stage: REPLAY_STAGE_DONE,
  });
  return frames;
}

/**
 * What the collapsed trail says while a replay is running. `null` means the
 * turn is not being replayed, and the caller shows `trailSummary` instead.
 */
export function replayLabel(stage: number, queryCount: number): string | null {
  if (stage >= REPLAY_STAGE_DONE) return null;
  if (stage === REPLAY_STAGE_PLANNING) return "Planning…";
  if (stage <= queryCount) return `Searching… ${stage} of ${queryCount}`;
  if (stage === queryCount + 1) return "Writing…";
  return "Reviewing…";
}

/* ── Dates ───────────────────────────────────────────────────────────── */

/** `14:20` → `2:20 PM`. Twelve-hour because that is the design's copy; no
 *  locale, so it renders identically on every machine — the same reason the
 *  dates never go through `Date` parsing here. */
export function clockLabel(stamp: string): string {
  const time = timePart(stamp);
  if (!time) return "";
  const [h, m] = time.split(":").map(Number);
  const suffix = h < 12 ? "AM" : "PM";
  const hour = h % 12 === 0 ? 12 : h % 12;
  return `${hour}:${m.toString().padStart(2, "0")} ${suffix}`;
}

/** `2026-09-18` → `Sep 18`, and `Sep 18, 2025` once the year differs from
 *  `now` — a bare `Sep 18` a year old would read as this month. */
export function monthDay(stamp: string, now: string): string {
  const [y, m, d] = datePart(stamp).split("-");
  const month = MONTHS[Number(m) - 1];
  if (!month) return datePart(stamp);
  const sameYear = y === datePart(now).slice(0, 4);
  return sameYear ? `${month} ${Number(d)}` : `${month} ${Number(d)}, ${y}`;
}

/** Minutes between two stamps on the SAME day, or `null` otherwise. */
function minutesApartToday(stamp: string, now: string): number | null {
  if (datePart(stamp) !== datePart(now)) return null;
  const a = timePart(stamp);
  const b = timePart(now);
  if (!a || !b) return null;
  const toMinutes = (t: string) => {
    const [h, m] = t.split(":").map(Number);
    return h * 60 + m;
  };
  return toMinutes(b) - toMinutes(a);
}

/**
 * When a thread began: `Today, 9:42 AM`, `Yesterday`, `Sep 18`.
 *
 * The clock survives only for today. A start time is context for a thread you
 * are still in; once it is yesterday's, the day is the whole answer.
 */
export function formatStarted(stamp: string, now: string): string {
  const day = datePart(stamp);
  const today = datePart(now);
  if (day === today) {
    const clock = clockLabel(stamp);
    return clock ? `Today, ${clock}` : "Today";
  }
  if (day === previousDay(today)) return "Yesterday";
  return monthDay(stamp, now);
}

/**
 * When a thread was last touched: `12 min ago`, `Today, 9:42 AM`,
 * `Yesterday, 4:18 PM`, `Sep 19`.
 *
 * The last hour is counted in minutes because that is the only window where
 * "how long ago" is what the reader wants; beyond it a wall clock is easier to
 * place. A stamp in the future (a clock skew, never real data) falls back to
 * the wall clock rather than rendering "-3 min ago".
 */
export function formatActivity(stamp: string, now: string): string {
  const minutes = minutesApartToday(stamp, now);
  if (minutes !== null && minutes >= 0 && minutes < 60) {
    return minutes <= 1 ? "just now" : `${minutes} min ago`;
  }
  const day = datePart(stamp);
  const today = datePart(now);
  const clock = clockLabel(stamp);
  if (day === today) return clock ? `Today, ${clock}` : "Today";
  if (day === previousDay(today)) {
    return clock ? `Yesterday, ${clock}` : "Yesterday";
  }
  return monthDay(stamp, now);
}

/* ── Composer mentions ───────────────────────────────────────────────── */

/**
 * Whether the mention picker should be open for this draft.
 *
 * It opens on a TRAILING `@` only, the way the prototype does — there is no
 * type-ahead filter behind it because there is no paper search to filter
 * against yet. The `@` must also start a word, which is the same rule
 * `lib/mentions.ts` holds on the Chat composer: without it, an email address
 * typed into the box opens a paper picker.
 */
export function mentionOpen(draft: string): boolean {
  if (!draft.endsWith("@")) return false;
  const before = draft[draft.length - 2];
  return before === undefined || /\s/.test(before);
}

/**
 * Replace the trailing `@` with the picked paper, leaving the caret after a
 * space so the sentence can continue. The title is inserted WHOLE and never
 * truncated: the prompt has to read as what the user sees.
 */
export function insertMention(draft: string, title: string): string {
  return `${draft.slice(0, -1)}@${title} `;
}

/* ── List copy ───────────────────────────────────────────────────────── */

/**
 * The outcome line under a row in "Recent explorations". Zero added is spelled
 * out rather than shown as "0 papers added": an exploration that added nothing
 * is a legitimate result in this design — Explorer is allowed to argue against
 * adding something.
 */
export function outcomeLabel(row: {
  added: number;
  exchanges: number;
  considered: number;
}): string {
  const added =
    row.added === 0 ? "nothing added" : `${plural(row.added, "paper")} added`;
  return `${added} · ${plural(row.exchanges, "exchange")} · ${row.considered} considered`;
}
