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
