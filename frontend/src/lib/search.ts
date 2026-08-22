/**
 * The filter behind every list page's search box.
 *
 * PURE and in `lib/` for the usual reason: vitest here runs in the node
 * environment with no jsdom, so a rule worth testing cannot live inside a
 * component.
 *
 * Deliberately a filter over data already in memory, not a query. All three
 * list endpoints return the whole list today, so filtering client-side is
 * instant and cannot disagree with what is on screen. If a list ever
 * paginates, this stops being enough -- and the fix is a real search
 * endpoint, not a bigger page.
 */

/**
 * Does `query` match this row?
 *
 * Every whitespace-separated term must appear somewhere in `fields`, so
 * typing more always NARROWS -- an OR would widen as the user typed, which
 * reads as the box being broken. Terms match as substrings rather than whole
 * words, because "uav" should find "Multi-UAV" and a word-boundary rule
 * would silently miss exactly the hyphenated and camel-cased titles this
 * domain is full of.
 *
 * Fields are joined with a newline, which no term can contain -- so a term
 * can never match by spanning the seam between two fields and appearing to
 * find text that is not there.
 *
 * An empty or whitespace-only query matches everything: the box is a filter,
 * and an empty filter removes nothing.
 *
 * Not normalized for diacritics: "Guven" does not find "Güven". Worth doing
 * if it ever bites, but it needs a decision about which normalization, and
 * guessing wrong makes matches that look like bugs.
 */
export function matchesQuery(
  query: string,
  fields: readonly (string | null | undefined)[]
): boolean {
  const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (terms.length === 0) return true;
  const haystack = fields
    .filter((f): f is string => typeof f === "string" && f.length > 0)
    .join("\n")
    .toLowerCase();
  return terms.every((term) => haystack.includes(term));
}
