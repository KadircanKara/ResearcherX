/**
 * The trust promise in the reader's terms, and the machinery behind a
 * disclosure for whoever wants it. The four steps above already demonstrate
 * each promise, so this section states them once rather than as a grid.
 */
export function Outcomes() {
  return (
    <section id="why" className="border-site-line scroll-mt-20 border-t py-24 sm:py-32">
      <div className="mx-auto max-w-[1180px] px-5 sm:px-8">
        <div className="grid gap-10 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)] lg:gap-16">
          <h2 className="text-site-fg max-w-md text-3xl font-semibold sm:text-[2.75rem] sm:leading-[1.1]">
            Built for work you have to defend.
          </h2>
          <div className="text-site-muted max-w-2xl space-y-5 text-[17px] leading-relaxed">
            <p>
              <span className="text-site-fg font-medium">Nothing is said without a source.</span>{" "}
              Every sentence of an answer points at a passage you can open, and a sentence with
              no marker makes no claim.
            </p>
            <p>
              <span className="text-site-fg font-medium">The search goes where you send it.</span>{" "}
              Name papers with @, or name one in your question, and the answer tells you when it
              searched only those.
            </p>
            <p>
              <span className="text-site-fg font-medium">Your sources stay beside your draft.</span>{" "}
              The library and the manuscript live in one project, so a claim you checked is one
              click from the sentence you cite it in.
            </p>
          </div>
        </div>

        <details className="group border-site-line mt-16 rounded-xl border">
          <summary className="text-site-fg flex cursor-pointer list-none items-center justify-between gap-4 px-5 py-4 text-[15px] font-medium [&::-webkit-details-marker]:hidden">
            How it&rsquo;s built
            <span className="text-site-muted text-[13px] font-normal group-open:hidden">Show</span>
            <span className="text-site-muted hidden text-[13px] font-normal group-open:inline">Hide</span>
          </summary>
          <div className="border-site-line text-site-muted grid gap-6 border-t px-5 py-5 text-[15px] leading-relaxed md:grid-cols-2">
            <p>
              Papers are split along their own section structure, taken from the PDF outline when
              there is one, so a passage keeps the heading and page it came from.
            </p>
            <p>
              Retrieval combines meaning-based search with keyword search, then reranks the
              candidates before the model reads them.
            </p>
            <p>
              After the answer is written, citation markers that point at the wrong paper are
              removed, and markers are renumbered in reading order.
            </p>
            <p>
              LaTeX compiles in a separate sandboxed service with no access to secrets or the
              database, with SyncTeX for source-to-PDF navigation.{" "}
              <a
                href="https://github.com/KadircanKara/ResearcherX"
                target="_blank"
                rel="noreferrer"
                className="text-site-fg underline decoration-site-line underline-offset-4 hover:decoration-current"
              >
                Read the source
              </a>
              .
            </p>
          </div>
        </details>
      </div>
    </section>
  );
}
