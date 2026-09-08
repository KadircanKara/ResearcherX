/**
 * Where an excerpt sits in its paper, formatted for display — or nothing.
 *
 * PURE and in `src/lib/` on purpose: vitest runs in the node environment here
 * with no jsdom and no React Testing Library, so a rule that lives inside a
 * component cannot be tested at all. This is the rule worth pinning; the
 * component around it is markup.
 *
 * Returns "" for every absent case, and the caller renders nothing at all
 * rather than an empty label. That is not a nicety: every chunk indexed
 * before structured chunking has `section: []` and `page: null`, and until
 * the re-index runs that is the whole corpus. Those cards must look exactly
 * as they did before this existed — a stray "·" or a bare "Section:" on
 * thousands of citations would be a visible regression shipped to make an
 * invisible feature look present.
 *
 * The separator is " > ", matching `chunk_header.SEPARATOR` on the backend,
 * which is what the model itself is shown. Reader and model describe a
 * location the same way.
 */
export function formatChunkLocator(
  section?: string[] | null,
  page?: number | null
): string {
  // Empty elements are dropped rather than joined: the backend's
  // `_section_tuple` holds the same line, and a null slipping through would
  // otherwise render as "IV. RL >  > B. Reward".
  const path = (section ?? []).filter((s) => typeof s === "string" && s.trim() !== "");
  const parts: string[] = [];
  if (path.length > 0) parts.push(path.join(" > "));
  // `page ?? null` rather than a truthiness test — page 0 is not a real
  // page number in this data, but a falsy check here is the kind of thing
  // that silently eats a legitimate value the day the data changes.
  if (page !== null && page !== undefined) parts.push(`p. ${page}`);
  return parts.join(" · ");
}
