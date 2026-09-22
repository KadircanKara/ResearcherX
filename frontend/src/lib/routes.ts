/**
 * Every in-app URL is built here and nowhere else.
 *
 * The app lives under `ADMIN_BASE` so the marketing landing page can own "/".
 * A route string typed at a call site is a copy of this rule that drifts the
 * next time the prefix moves, so pages, links and `pathname` checks all read
 * from these helpers. Only API paths (`/v1/...`, in `lib/api.ts`) are exempt:
 * they address the backend, not a page.
 *
 * Pure: no React, no Next imports, so it is testable under vitest's node
 * environment like every other rule in `src/lib/`.
 */
export const ADMIN_BASE = "/admin";

const research = `${ADMIN_BASE}/research`;
const explorer = `${ADMIN_BASE}/explorer`;

export const routes = {
  /** The app's own front door; the landing page sends "Open the app" here. */
  home: () => ADMIN_BASE,
  research: () => research,
  project: (id: string) => `${research}/${id}`,
  chat: (id: string) => `${research}/${id}/chat`,
  conversation: (id: string, cid: string) => `${research}/${id}/chat/${cid}`,
  papers: (id: string) => `${research}/${id}/papers`,
  graph: (id: string) => `${research}/${id}/graph`,
  latex: (id: string) => `${research}/${id}/latex`,
  latexDoc: (id: string, docId: string) => `${research}/${id}/latex/${docId}`,
  explorer: () => explorer,
  exploration: (eid: string) => `${explorer}/${eid}`,
} as const;
