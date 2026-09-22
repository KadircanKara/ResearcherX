/**
 * The projects list's own rules: which layout it uses, what the search box
 * matches, how the keyword field is read, and what a project with no
 * description says.
 *
 * The layout parse lives here rather than inline at the read site because the
 * stored value is whatever a previous build (or the user's devtools) left in
 * localStorage -- anything unrecognised has to fall back rather than render a
 * layout nothing handles. Pure (no React, no DOM) so it is testable: vitest
 * here runs in the node environment with no jsdom.
 */

export const PROJECT_VIEWS = ["card", "list"] as const;

export type ProjectView = (typeof PROJECT_VIEWS)[number];

export const PROJECT_VIEW_KEY = "rx.research.view";

export const DEFAULT_PROJECT_VIEW: ProjectView = "card";

export function parseProjectView(value: string | null | undefined): ProjectView {
  return (PROJECT_VIEWS as readonly string[]).includes(value ?? "")
    ? (value as ProjectView)
    : DEFAULT_PROJECT_VIEW;
}

/**
 * The projects whose title or any topic keyword contains `query`, ignoring
 * case. A blank query (whitespace included) keeps every project, so a stray
 * space in the search box never reads as "nothing matches".
 */
export function filterProjects<T extends { title: string; topic_keywords: string[] }>(
  projects: T[],
  query: string,
): T[] {
  const q = query.trim().toLowerCase();
  if (!q) return projects;
  return projects.filter(
    (p) =>
      p.title.toLowerCase().includes(q) ||
      p.topic_keywords.some((k) => k.toLowerCase().includes(q)),
  );
}

/** The new-project keyword field: `"carbon, energy ,, policy"` → three keywords. */
export function parseKeywords(input: string): string[] {
  return input
    .split(",")
    .map((k) => k.trim())
    .filter(Boolean);
}

export const NO_DESCRIPTION = "No description yet.";

/**
 * The line a card or row shows under a project's title.
 *
 * The prototype STORES "No description yet." when the field is left blank;
 * here the server keeps `null` and the placeholder is only ever rendered, so
 * it never becomes a description the user has to delete.
 */
export function projectBlurb(project: { description: string | null }): string {
  return project.description?.trim() || NO_DESCRIPTION;
}
