"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { createRun } from "@/lib/api";

export function QueryForm() {
  const router = useRouter();
  const [question, setQuestion] = useState(
    "ML and AI methods used in cooperative multi-UAV systems",
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (question.trim().length < 5) {
      setError("Question must be at least 5 characters.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const run = await createRun(question.trim());
      router.push(`/research/${run.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create run.");
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-3">
      <label className="block">
        <span className="text-sm font-mono text-ink/60">question</span>
        <textarea
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          rows={3}
          placeholder="e.g. What are the tradeoffs of using Arq vs Celery for Python background jobs in 2026?"
          className="mt-1 w-full rounded-md border border-ink/15 bg-white px-3 py-2 font-sans text-base outline-none focus:border-ink/40"
          disabled={submitting}
        />
      </label>
      {error && <p className="text-sm text-red-600 font-mono">{error}</p>}
      <button
        type="submit"
        disabled={submitting}
        className="rounded-md bg-ink px-4 py-2 text-sm font-mono text-paper disabled:opacity-50"
      >
        {submitting ? "starting…" : "research"}
      </button>
    </form>
  );
}
