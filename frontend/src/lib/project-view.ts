/**
 * How the research page lays its projects out.
 *
 * The parse lives here rather than inline at the read site because the stored
 * value is whatever a previous build (or the user's devtools) left in
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
