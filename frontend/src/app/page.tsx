import type { Metadata } from "next";
import { LandingPage } from "@/components/landing/landing-page";

const TITLE = "ResearcherX — Ask your papers";
const DESCRIPTION =
  "A workspace for research papers. Upload PDFs or URLs, ask anything, and every sentence of the answer cites the section and page it came from. Includes a multi-file LaTeX editor with SyncTeX.";

export const metadata: Metadata = {
  title: TITLE,
  description: DESCRIPTION,
  openGraph: { title: TITLE, description: DESCRIPTION, type: "website" },
  twitter: { card: "summary_large_image" },
};

export default function Home() {
  return <LandingPage />;
}
