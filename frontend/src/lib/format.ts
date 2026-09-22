/**
 * Formatting primitives more than one screen reads.
 *
 * These used to live in `lib/explorer.ts`, which Chat then imported — a file
 * named for one screen holding another's rules. They moved here when Explorer
 * was redesigned and wanted a DIFFERENT presentation of the same stamps
 * (`Sep 18` and a twelve-hour clock, against Chat's `18 Sep 2026` and a
 * twenty-four-hour one). What is shared is the substrate, not the wording: the
 * two screens compose these, and each owns how its own dates read.
 *
 * Dates are plain `YYYY-MM-DD[THH:MM]` strings and never go through
 * `new Date(...)` local parsing. Explorer is server-rendered and then
 * hydrated, and a server in one timezone disagreeing with a browser in another
 * is a hydration mismatch that only shows up on someone else's machine.
 */

export const MONTHS = [
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
export function previousDay(day: string): string {
  const [y, m, d] = day.split("-").map(Number);
  const t = Date.UTC(y, m - 1, d) - 86_400_000;
  return new Date(t).toISOString().slice(0, 10);
}

/** `1 paper` / `2 papers`. One pluralizer, because two can disagree. */
export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** `2026-09-18` → `18 Sep`: the day without its year, for a column that is
 *  already scoped to recent activity. Same no-`Date` parsing as `formatDate`. */
export function formatShortDay(stamp: string): string {
  const [, m, d] = datePart(stamp).split("-");
  const month = MONTHS[Number(m) - 1];
  if (!month) return datePart(stamp);
  return `${Number(d)} ${month}`;
}

/**
 * "just now" / "12 min ago" / "2h ago" / "Yesterday" / "Sep 18" -- how long
 * ago an ISO timestamp was, relative to `now`.
 *
 * Unlike the functions above this one has to parse the stamp: an age is a
 * difference of instants. It is called from client components only, after
 * the data has been fetched, so server and browser never both render it.
 * `now` is a parameter so the thresholds are testable.
 */
export function relativeLabel(iso: string, now: Date = new Date()): string {
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return iso;
  const mins = Math.round((now.getTime() - then.getTime()) / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  if (hours < 48) return "Yesterday";
  return `${MONTHS[then.getUTCMonth()]} ${then.getUTCDate()}`;
}

/** `Ada Kim` → `AK`: up to two initials, for an avatar tile. */
export function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .map((part) => part[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
}
