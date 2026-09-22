import { Reveal } from "./reveal";

const lines = [
  "No general knowledge. Only the papers you gave it.",
  "A marker on the sentence that makes the claim, not somewhere nearby.",
  "A citation that points at the wrong paper is removed, deterministically.",
];

export function Principles() {
  return (
    <section className="bg-lp-paper text-lp-ink">
      <div className="mx-auto max-w-[1240px] px-5 py-24 sm:px-8 sm:py-32">
        <Reveal>
          <p className="label-xs text-lp-gray-3">Principles</p>
        </Reveal>
        <div className="border-lp-ink/15 mt-10 border-t">
          {lines.map((l, i) => (
            <Reveal
              key={l}
              delay={i * 110}
              className="border-lp-ink/15 border-b py-10 sm:py-14"
            >
              <p className="serif-italic max-w-4xl text-2xl leading-[1.25] sm:text-4xl lg:text-[2.75rem]">
                {l}
              </p>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}
