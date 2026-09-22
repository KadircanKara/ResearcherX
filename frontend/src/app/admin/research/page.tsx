import type { Metadata } from "next";
import { ProjectsPage } from "@/components/projects/projects-page";

const TITLE = "Projects — ResearcherX";

/**
 * A server component only so it can carry the prototype's page metadata; the
 * list itself is a client component and makes every backend call from the
 * browser (no server-side fetch -- see the repo's CLAUDE.md).
 */
export const metadata: Metadata = {
  title: TITLE,
  description:
    "Every research workspace in ResearcherX: papers, conversations and LaTeX documents, grouped by project.",
  openGraph: {
    title: TITLE,
    description: "Organise your research into focused workspaces.",
    type: "website",
  },
  twitter: { card: "summary_large_image" },
};

export default function ResearchPage() {
  return <ProjectsPage />;
}
