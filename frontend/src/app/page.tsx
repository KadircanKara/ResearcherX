import { QueryForm } from "@/components/query-form";

export default function Home() {
  return (
    <div className="space-y-8">
      <section>
        <h1 className="text-3xl font-semibold tracking-tight">
          Ask a research question.
        </h1>
        <p className="mt-2 text-ink/60">
          A planner decomposes your question, searchers fan out in parallel, a
          synthesizer drafts a cited report, and a critic checks it.
        </p>
      </section>
      <QueryForm />
      <section className="border-t border-ink/10 pt-6 text-sm text-ink/60 font-mono">
        <div>pipeline: planner → searchers (parallel) → synthesizer → critic</div>
        <div className="mt-1">provider: groq · llama-3.3-70b-versatile</div>
      </section>
    </div>
  );
}
