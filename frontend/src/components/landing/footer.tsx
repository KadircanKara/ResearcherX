import { HERO_VIDEO_CREDIT } from "@/lib/landing-video";

export function Footer() {
  return (
    <footer className="bg-lp-ink text-lp-paper border-lp-paper/15 border-t">
      <div className="mx-auto flex max-w-[1240px] flex-col gap-3 px-5 py-8 text-[12px] sm:flex-row sm:items-center sm:justify-between sm:px-8">
        <span className="text-lp-paper font-semibold">ResearcherX</span>
        <div className="text-lp-paper/45 flex flex-col gap-1 sm:items-end">
          <span>{HERO_VIDEO_CREDIT}</span>
          <span>© {new Date().getFullYear()} ResearcherX. All rights reserved.</span>
        </div>
      </div>
    </footer>
  );
}
