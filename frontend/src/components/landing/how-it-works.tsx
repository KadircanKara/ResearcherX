import { Reveal } from "./reveal";

const steps = [
  {
    n: "01",
    title: "Ingest",
    body: "Drop a PDF or paste a URL. The paper is read, split along its own section outline, and embedded — usually under a minute for twenty pages.",
  },
  {
    n: "02",
    title: "Ask",
    body: "Ask in plain language. Retrieval searches by meaning and by exact words, fuses both, then reranks. Type @ to keep a question inside one paper.",
  },
  {
    n: "03",
    title: "Cite",
    body: "Every sentence carries a marker; hover it to read the passage. A claim the papers do not support is refused, not invented.",
  },
];

export function HowItWorks() {
  return (
    <section id="how-it-works" className="bg-lp-paper text-lp-ink scroll-mt-16">
      <div className="mx-auto max-w-[1240px] px-5 py-24 sm:px-8 sm:py-32">
        <Reveal>
          <p className="label-xs text-lp-gray-3">How it works</p>
          <h2 className="mt-5 max-w-2xl text-3xl font-semibold sm:text-5xl">
            Three steps, no ceremony.
          </h2>
        </Reveal>

        <ul className="border-lp-ink/15 mt-16 grid gap-px border-t md:grid-cols-3">
          {steps.map((s, i) => (
            <Reveal as="li" key={s.n} delay={i * 90} className="md:border-lp-ink/15 pt-10 md:border-l md:px-8 md:first:border-l-0 md:first:pl-0">
              <span className="text-lp-gray-2 block text-5xl font-semibold sm:text-6xl">
                {s.n}
              </span>
              <h3 className="mt-6 text-xl font-semibold">{s.title}</h3>
              <p className="text-lp-gray-4 mt-3 max-w-md text-[15px] leading-relaxed">
                {s.body}
              </p>
            </Reveal>
          ))}
        </ul>
      </div>
    </section>
  );
}
