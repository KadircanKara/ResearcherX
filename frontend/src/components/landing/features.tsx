import { Reveal } from "./reveal";

const features = [
  {
    title: "Section-bounded chunking",
    body: "Papers are split along their own outline, so a heading never gets separated from its text.",
    wide: true,
  },
  {
    title: "Hybrid retrieval",
    body: "Meaning-based and exact-word search, fused, then reranked by a cross-encoder.",
  },
  {
    title: "Scope with @",
    body: "Name one or more papers in the question and the search stays inside them.",
  },
  {
    title: "Refuses to guess",
    body: "If the ingested papers do not cover it, the answer says exactly that.",
  },
  {
    title: "Citations that locate",
    body: "Section and page on every marker, and the passage on hover.",
  },
  {
    title: "Projects you can share",
    body: "Invite teammates; papers, chats and LaTeX documents live in one project.",
  },
];

export function Features() {
  return (
    <section id="features" className="bg-lp-paper text-lp-ink scroll-mt-16">
      <div className="mx-auto max-w-[1240px] px-5 py-24 sm:px-8 sm:py-32">
        <Reveal>
          <p className="label-xs text-lp-gray-3">Features</p>
          <h2 className="mt-5 max-w-2xl text-3xl font-semibold sm:text-5xl">
            Built to be checkable.
          </h2>
        </Reveal>

        <div className="mt-14 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {features.map((f, i) => (
            <Reveal
              key={f.title}
              delay={i * 70}
              className={`border-lp-ink flex flex-col justify-between border p-7 transition-colors duration-200 hover:bg-lp-ink hover:text-lp-paper ${
                f.wide ? "lg:col-span-2" : ""
              }`}
            >
              <h3 className="text-lg font-semibold">{f.title}</h3>
              <p className="mt-3 max-w-md text-[15px] leading-relaxed opacity-70">
                {f.body}
              </p>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}
