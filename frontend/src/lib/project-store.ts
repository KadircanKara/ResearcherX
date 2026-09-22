/**
 * Cross-component notification of project field edits.
 *
 * The sidebar (`app-shell`) loads its project list once and the colour picker
 * lives in the project header, several trees away -- nothing connects them, so
 * a colour picked on the project page left the sidebar dot on the old colour
 * until the next full load. Module-level listeners rather than a context: the
 * two components share no ancestor below the shell, and a context spanning
 * them would re-render the whole app on every publish.
 *
 * Pure (no React, no fetch) so it is testable -- vitest here runs in the node
 * environment with no jsdom.
 */

export type ProjectColorChange = { id: string; color: string };

const listeners = new Set<(change: ProjectColorChange) => void>();

/**
 * Announce the colour a project should now be painted.
 *
 * Called for the optimistic pick AND for the revert when the PATCH fails, so
 * a subscriber never has to know which of the two it is looking at.
 */
export function publishProjectColor(change: ProjectColorChange) {
  listeners.forEach((fn) => fn(change));
}

export function subscribeProjectColor(fn: (change: ProjectColorChange) => void) {
  listeners.add(fn);
  // Braces matter: Set.delete returns boolean, which is not a valid
  // useEffect destructor return type.
  return () => {
    listeners.delete(fn);
  };
}

/*
 * "The project list changed" -- a project was created. The sidebar fetches
 * its list once per identity, so without this a new project appeared there
 * only after a full reload. Carries no payload: the subscriber refetches
 * rather than splicing in a row whose shape (counts, members) only the list
 * endpoint knows.
 */
const listChangedListeners = new Set<() => void>();

export function publishProjectListChanged() {
  listChangedListeners.forEach((fn) => fn());
}

export function subscribeProjectListChanged(fn: () => void) {
  listChangedListeners.add(fn);
  return () => {
    listChangedListeners.delete(fn);
  };
}
