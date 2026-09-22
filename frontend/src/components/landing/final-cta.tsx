import { routes } from "@/lib/routes";
import { Reveal } from "./reveal";

export function FinalCta() {
  return (
    <section className="bg-lp-ink text-lp-paper">
      <div className="mx-auto max-w-[1240px] px-5 py-28 text-center sm:px-8 sm:py-36">
        <Reveal>
          <h2 className="text-4xl font-semibold sm:text-6xl lg:text-7xl">
            Bring your papers.
          </h2>
          <div className="mt-10 flex justify-center">
            <a href={routes.home()} className="pill-light px-8 py-4 text-sm font-medium">
              Open the app
            </a>
          </div>
          <p className="mt-8">
            <a
              href="https://github.com/KadircanKara/ResearcherX"
              target="_blank"
              rel="noreferrer"
              className="text-lp-paper/55 hover:text-lp-paper text-[13px] underline underline-offset-4 transition-colors"
            >
              Source on GitHub
            </a>
          </p>
        </Reveal>
      </div>
    </section>
  );
}
