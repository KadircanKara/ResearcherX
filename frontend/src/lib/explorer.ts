/**
 * Pure formatting rules for the Explorer screen.
 *
 * Everything here is a rule the design states, so it lives outside the
 * components and is tested: this repo runs vitest in the NODE environment with
 * no jsdom, so logic left inside a component is logic that is never tested.
 *
 * Dates are handled as plain `YYYY-MM-DD[THH:MM]` strings and never through
 * `new Date(...)` local parsing: the Explorer view is server-rendered and then
 * hydrated, and a server in one timezone disagreeing with a browser in another
 * is a hydration mismatch that only shows up on someone else's machine.
 */

const MONTHS = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

/** The date half of a `YYYY-MM-DD[THH:MM]` stamp. */
export function datePart(stamp: string): string {
  return stamp.slice(0, 10);
}

/** The `HH:MM` half, or `""` when the stamp carries no time. */
export function timePart(stamp: string): string {
  return stamp.length >= 16 ? stamp.slice(11, 16) : "";
}

/** `2026-08-16` → `16 Aug 2026`. No locale, so it renders identically
 *  everywhere — the same reason dates never go through `Date` parsing here. */
export function formatDate(stamp: string): string {
  const [y, m, d] = datePart(stamp).split("-");
  const month = MONTHS[Number(m) - 1];
  if (!month) return datePart(stamp);
  return `${Number(d)} ${month} ${y}`;
}

/** The calendar day before `YYYY-MM-DD`, computed in UTC so month and year
 *  boundaries are handled without a timezone ever entering into it. */
function previousDay(day: string): string {
  const [y, m, d] = day.split("-").map(Number);
  const t = Date.UTC(y, m - 1, d) - 86_400_000;
  return new Date(t).toISOString().slice(0, 10);
}

/**
 * Last-activity column: `Today, 15:10`, `Yesterday, 10:22`, `16 Aug 2026`.
 * Older stamps drop the time — a clock is only meaningful while "today" and
 * "yesterday" still locate it.
 */
export function formatActivity(stamp: string, now: string): string {
  const day = datePart(stamp);
  const today = datePart(now);
  const time = timePart(stamp);
  if (day === today) return time ? `Today, ${time}` : "Today";
  if (day === previousDay(today)) return time ? `Yesterday, ${time}` : "Yesterday";
  return formatDate(stamp);
}

/** Thread header: "started today" / "started yesterday" / "started 2 Aug 2026". */
export function startedLabel(stamp: string, now: string): string {
  const day = datePart(stamp);
  const today = datePart(now);
  if (day === today) return "today";
  if (day === previousDay(today)) return "yesterday";
  return formatDate(stamp);
}

function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

/**
 * Outcome column. Zero is spelled out rather than shown as "0 papers added":
 * an exploration that added nothing is a legitimate result in this design —
 * the assistant is allowed to argue against adding something.
 */
export function outcomeLabel(added: number): string {
  return added === 0 ? "nothing added" : `${plural(added, "paper")} added`;
}

export function exchangesLabel(exchanges: number): string {
  return plural(exchanges, "exchange");
}

/** List header: "5 explorations · 7 papers added in total". */
export function listSummary(
  explorations: readonly { added: number }[]
): string {
  const added = explorations.reduce((sum, e) => sum + e.added, 0);
  return `${plural(explorations.length, "exploration")} · ${plural(
    added,
    "paper"
  )} added in total`;
}

/** Thread header, second line: "1 paper added · 6 considered". */
export function threadOutcome(added: number, considered: number): string {
  return `${outcomeLabel(added)} · ${considered} considered`;
}

/**
 * The distance column. A candidate already in the library has no distance to
 * the library — an em dash, not a zero, because zero is a real distance and
 * would read as "identical to something you have".
 */
export function formatDistance(distance: number | null): string {
  return distance === null ? "—" : distance.toFixed(2);
}
