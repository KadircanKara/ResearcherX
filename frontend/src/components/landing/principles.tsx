import { Reveal } from "./reveal";

const principles = [
  {
    title: "Only your papers",
    body: "No general knowledge. Every answer is written from the papers you gave it, and from nothing else.",
  },
  {
    title: "The marker sits on the claim",
    body: "A citation goes on the sentence that makes the claim, not somewhere nearby. If a sentence has no marker, it made no claim.",
  },
  {
    title: "Wrong citations are removed",
    body: "A marker that points at a paper the sentence is not about is stripped, deterministically, before the answer reaches you.",
  },
];

export function Principles() {
  return (
    <section className="bg-lp-paper text-lp-ink">
      <div className="mx-auto max-w-[1240px] px-5 py-24 sm:px-8 sm:py-32">
        <Reveal>
          <p className="label-xs text-lp-gray-3">Principles</p>
          <h2 className="mt-5 max-w-2xl text-3xl font-semibold sm:text-5xl">
            Three rules it never bends.
          </h2>
        </Reveal>

        <div className="mt-14 grid gap-5 md:grid-cols-3">
          {principles.map((p, i) => (
            <Reveal
              key={p.title}
              delay={i * 90}
              className="border-lp-ink flex flex-col border p-7 transition-colors duration-200 hover:bg-lp-ink hover:text-lp-paper"
            >
              <span className="label-xs opacity-50">0{i + 1}</span>
              <h3 className="mt-6 text-lg font-semibold">{p.title}</h3>
              <p className="mt-3 text-[15px] leading-relaxed opacity-70">{p.body}</p>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}
