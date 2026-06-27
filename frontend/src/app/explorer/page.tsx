"use client";

import { useState } from "react";
import { ChatBox } from "@/components/explorer/chat-box";
import { ResearchAccordion } from "@/components/explorer/research-accordion";
import { ResultCard, type ResultCardData } from "@/components/explorer/result-card";

const SUGGESTIONS = [
  "decentralized multi-UAV coordination",
  "vision transformers for aerial imagery",
  "direct preference optimization",
];

const RESULTS: ResultCardData[] = [
  {
    title: "QMIX-Lite: Lightweight Value Decomposition for Large Swarms",
    meta: "R. Okafor · 2024 · 41 citations · arXiv",
    abstract:
      "A lighter monotonic mixing network that holds up as agent count grows past 30.",
    matched: ["value decomposition", "scaling"],
    missing: [],
    neutral: ["QMIX"],
    relevance: 81,
  },
  {
    title: "Decentralized Coordination of Multi-UAV Swarms via Attention Policies",
    meta: "A. Chen, J. Park · 2023 · 142 citations · arXiv",
    abstract:
      "A decentralized attention-based policy for coordinating large UAV swarms under partial observability.",
    matched: ["swarm", "multi-agent"],
    missing: ["decentralized coordination"],
    relevance: 67,
  },
];

export default function ExplorerPage() {
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [followUp, setFollowUp] = useState("");

  function submit(value?: string) {
    const q = (value ?? query).trim();
    if (!q) return;
    setQuery(q);
    setSubmitted(true);
  }

  if (!submitted) {
    return (
      <div className="mx-auto flex min-h-[calc(100vh-3rem)] max-w-2xl flex-col items-center justify-center px-6 text-center">
        <h1 className="text-3xl font-semibold tracking-tight">
          How can I <span className="text-gradient">help research</span> today?
        </h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Ask for papers or pose a question — I search arXiv &amp; Semantic
          Scholar and route findings into your projects.
        </p>

        <div className="mt-6 w-full">
          <ChatBox
            value={query}
            onChange={setQuery}
            onSubmit={() => submit()}
            placeholder="Ask for papers or pose a question…"
          />
        </div>

        <div className="mt-4 flex flex-wrap justify-center gap-2">
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => submit(s)}
              className="rounded-full border border-border bg-card px-3 py-1.5 text-[12.5px] text-muted-foreground transition-colors hover:border-primary hover:text-accent-foreground"
            >
              {s}
            </button>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-3xl px-6 py-8">
      {/* User query bubble */}
      <div className="ml-auto max-w-[80%] rounded-2xl rounded-br-sm gradient-brand px-3.5 py-2.5 text-[13.5px] text-white">
        {query}
      </div>

      {/* Research pipeline */}
      <div className="mt-6">
        <ResearchAccordion />
      </div>

      {/* Top matches */}
      <p className="mb-3 mt-6 font-mono text-xs text-muted-foreground">
        Top matches
      </p>
      <div className="flex flex-col gap-4">
        {RESULTS.map((r) => (
          <ResultCard key={r.title} {...r} />
        ))}
      </div>

      {/* Follow-up input */}
      <div className="mt-6">
        <ChatBox
          value={followUp}
          onChange={setFollowUp}
          onSubmit={() => setFollowUp("")}
          placeholder="Ask a follow-up…"
        />
      </div>
    </div>
  );
}
