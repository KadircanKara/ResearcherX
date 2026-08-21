/**
 * Selection algebra for the Edit / Select all / Clear / Done flow.
 *
 * Pure and in `lib/` because vitest here runs in the node environment with no
 * jsdom and no React Testing Library -- a rule worth testing cannot live
 * inside a component. Every function returns a NEW set, so React sees a
 * changed reference and re-renders.
 */

export function toggle(selected: ReadonlySet<string>, id: string): Set<string> {
  const next = new Set(selected);
  if (!next.delete(id)) next.add(id);
  return next;
}

/**
 * Union the VISIBLE ids into the selection.
 *
 * The parameter is what is on screen, never "everything on the server". These
 * lists are unpaginated today so the two coincide -- but the contract is the
 * visible set, so adding pagination later cannot turn Select all into a
 * delete of rows the user never saw.
 */
export function selectAll(
  selected: ReadonlySet<string>,
  ids: readonly string[]
): Set<string> {
  const next = new Set(selected);
  for (const id of ids) next.add(id);
  return next;
}

export function clear(): Set<string> {
  return new Set();
}

/** False for an empty list: nothing selected is not everything selected. */
export function isAllSelected(
  selected: ReadonlySet<string>,
  ids: readonly string[]
): boolean {
  return ids.length > 0 && ids.every((id) => selected.has(id));
}
