import { ArrowRight } from "lucide-react";
import { routes } from "@/lib/routes";

export function FinalCta() {
  return (
    <section className="px-3 pb-24 sm:px-8 sm:pb-32">
      <div className="bg-site-panel border-site-line mx-auto max-w-[1180px] rounded-2xl border px-6 py-16 text-center sm:py-24">
        <h2 className="text-site-fg text-4xl font-semibold sm:text-6xl">Bring your papers.</h2>
        <p className="text-site-muted mx-auto mt-5 max-w-lg text-[17px] leading-relaxed">
          Start a project, add what you are reading, and ask your first question.
        </p>
        <div className="mt-9 flex flex-col items-stretch justify-center gap-3 sm:flex-row sm:items-center">
          <a
            href={routes.home()}
            className="bg-site-accent text-site-accent-fg inline-flex h-12 items-center justify-center gap-2 rounded-lg px-6 text-[15px] font-medium transition-[filter] hover:brightness-110"
          >
            Open the app
            <ArrowRight className="size-4" aria-hidden />
          </a>
          <a
            href="https://github.com/KadircanKara/ResearcherX"
            target="_blank"
            rel="noreferrer"
            className="border-site-line text-site-fg hover:bg-site-bg inline-flex h-12 items-center justify-center rounded-lg border px-6 text-[15px] font-medium transition-colors"
          >
            View the source on GitHub
          </a>
        </div>
      </div>
    </section>
  );
}
