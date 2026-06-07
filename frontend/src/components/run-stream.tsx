"use client";

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { eventsUrl, getRun } from "@/lib/api";
import type { Critique, Finding, Plan, RunEvent, RunStatus, Validation } from "@/lib/types";

interface Props {
  runId: string;
}

export function RunStream({ runId }: Props) {
  const [question, setQuestion] = useState<string>("");
  const [status, setStatus] = useState<RunStatus>("pending");
  const [plan, setPlan] = useState<Plan | null>(null);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [validations, setValidations] = useState<Record<string, Validation>>({});
  const [report, setReport] = useState<string>("");
  const [critique, setCritique] = useState<Critique | null>(null);
  const [active, setActive] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [reportView, setReportView] = useState<"rendered" | "raw">("rendered");
  const sourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    let cancelled = false;

    // Findings can arrive twice (snapshot seed + buffered live event) —
    // dedupe by (query, attempts), which uniquely identifies an attempt.
    const addFinding = (f: Finding) =>
      setFindings((prev) =>
        prev.some(
          (p) => p.query === f.query && (p.attempts ?? 1) === (f.attempts ?? 1),
        )
          ? prev
          : [...prev, f],
      );

    function apply(data: RunEvent) {
      switch (data.type) {
        case "status":
          setStatus(data.status);
          break;
        case "agent_start":
          setActive(data.query ? `${data.agent} → ${data.query}` : data.agent);
          break;
        case "plan":
          setPlan(data.plan);
          break;
        case "finding":
          addFinding(data.finding);
          break;
        case "validation":
          setValidations((prev) => ({ ...prev, [data.query]: data }));
          setActive(`planner validation: ${data.verdict} — ${data.query}`);
          break;
        case "search_retry":
          setActive(
            `planner revising query (${data.attempt}/${data.max_attempts}): ` +
              `${data.old_query} → ${data.new_query}`,
          );
          break;
        case "report_delta":
          setReport((prev) => prev + data.text);
          break;
        case "critique":
          setCritique(data.critique);
          break;
        case "error":
          setErr(data.message);
          break;
      }
    }

    async function init() {
      // Subscribe BEFORE fetching the snapshot. The backend records each
      // step before publishing its event, so every event published before
      // our subscription opened is guaranteed to be in the snapshot's
      // `steps` — and everything after it arrives live. Events are buffered
      // until the seed is applied, then replayed (dedupe makes the overlap
      // harmless). GET-then-subscribe has a gap that loses fast events:
      // the plan lands <1s after run creation.
      const src = new EventSource(eventsUrl(runId));
      sourceRef.current = src;
      const buffer: RunEvent[] = [];
      let live = false;

      function handle(ev: MessageEvent) {
        try {
          const data = JSON.parse(ev.data) as RunEvent;
          if (live) apply(data);
          else buffer.push(data);
        } catch {
          // ignore malformed
        }
      }

      // Every backend event type must be listed here — EventSource only
      // fires listeners for named events, so unlisted types are dropped.
      for (const kind of [
        "status",
        "agent_start",
        "plan",
        "finding",
        "validation",
        "search_retry",
        "report_delta",
        "critique",
        "error",
      ]) {
        src.addEventListener(kind, handle);
      }
      src.addEventListener("end", () => {
        src.close();
        sourceRef.current = null;
      });

      // Wait until the subscription is open (or errors — then the snapshot
      // alone is still rendered) before taking the snapshot.
      await new Promise<void>((resolve) => {
        src.addEventListener("open", () => resolve(), { once: true });
        src.addEventListener("error", () => resolve(), { once: true });
      });

      try {
        const run = await getRun(runId);
        if (cancelled) return;
        setQuestion(run.question);
        setStatus(run.status);
        setReport(run.report ?? "");
        setErr(run.error ?? null);

        // Seed agent state from recorded steps — SSE is live-update only.
        for (const s of run.steps) {
          if (s.kind === "plan") {
            setPlan(s.output as unknown as Plan);
          } else if (s.kind === "search") {
            const f = s.output as unknown as Finding;
            if (f.validated || f.accepted_degraded) addFinding(f);
          } else if (s.kind === "validate") {
            const v = s.output as unknown as Omit<Validation, "query" | "attempt">;
            const query = String(s.input.sub_query ?? "");
            const attempt = Number(s.input.attempt ?? 1);
            setValidations((prev) => ({
              ...prev,
              [query]: { ...v, query, attempt },
            }));
          } else if (s.kind === "critique") {
            setCritique(s.output as unknown as Critique);
          }
        }
        setLoaded(true);

        if (run.status === "completed" || run.status === "failed") {
          src.close();
          sourceRef.current = null;
          return;
        }

        for (const ev of buffer) apply(ev);
        live = true;
      } catch (e) {
        if (!cancelled) {
          setLoadError(e instanceof Error ? e.message : "Failed to load run.");
        }
        src.close();
        sourceRef.current = null;
      }
    }

    init();

    return () => {
      cancelled = true;
      sourceRef.current?.close();
      sourceRef.current = null;
    };
  }, [runId]);

  if (loadError) {
    return (
      <div className="text-sm font-mono text-red-600">
        failed to load run {runId}: {loadError}
      </div>
    );
  }

  if (!loaded) {
    return <div className="text-sm font-mono text-ink/50">loading run {runId}…</div>;
  }

  return (
    <div className="space-y-8">
      <section>
        <h2 className="text-xs font-mono text-ink/50">question</h2>
        <p className="mt-1 text-lg">{question}</p>
        <p className="mt-2 text-xs font-mono text-ink/50">
          status: <span className="text-ink">{status}</span>
          {active && status === "running" && <span className="ml-3">{active}</span>}
        </p>
        {err && <p className="mt-2 text-sm text-red-600 font-mono">{err}</p>}
      </section>

      {plan && (
        <section>
          <h2 className="text-xs font-mono text-ink/50 mb-2">plan</h2>
          <p className="text-sm text-ink/70 italic">{plan.rationale}</p>
          <ul className="mt-2 list-disc pl-5 text-sm">
            {plan.sub_queries.map((q) => (
              <li key={q}>{q}</li>
            ))}
          </ul>
        </section>
      )}

      {findings.length > 0 && (
        <section>
          <h2 className="text-xs font-mono text-ink/50 mb-2">findings ({findings.length})</h2>
          <div className="space-y-3">
            {findings.map((f, i) => (
              <details key={i} className="rounded border border-ink/10 bg-white p-3">
                <summary className="cursor-pointer font-mono text-sm">
                  {f.query}
                  {f.validated && (
                    <span className="ml-2 rounded bg-green-100 px-1.5 py-0.5 text-xs text-green-700">
                      validated{(f.attempts ?? 1) > 1 ? ` · attempt ${f.attempts}` : ""}
                    </span>
                  )}
                  {f.accepted_degraded && (
                    <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-700">
                      best-effort ({f.attempts ?? 1}{" "}
                      {(f.attempts ?? 1) === 1 ? "attempt" : "attempts"})
                    </span>
                  )}
                </summary>
                {f.accepted_degraded &&
                  (validations[f.query]?.reasons?.length ?? 0) > 0 && (
                    <p className="mt-2 text-xs font-mono text-amber-700">
                      planner: {validations[f.query].reasons.join("; ")}
                    </p>
                  )}
                <p className="mt-2 text-sm">{f.summary}</p>
                {f.sources.length > 0 && (
                  <ul className="mt-2 text-xs font-mono text-ink/60 list-disc pl-5">
                    {f.sources.map((s, j) => (
                      <li key={j}>
                        <a href={s} target="_blank" rel="noreferrer" className="underline">
                          {s}
                        </a>
                      </li>
                    ))}
                  </ul>
                )}
              </details>
            ))}
          </div>
        </section>
      )}

      {report && (
        <section>
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-xs font-mono text-ink/50">report</h2>
            <div
              role="tablist"
              aria-label="Report view"
              className="inline-flex rounded-md border border-ink/15 bg-white p-0.5 text-xs font-mono"
            >
              {(["rendered", "raw"] as const).map((mode) => {
                const selected = reportView === mode;
                return (
                  <button
                    key={mode}
                    type="button"
                    role="tab"
                    aria-selected={selected}
                    onClick={() => setReportView(mode)}
                    className={
                      "rounded px-2.5 py-1 transition-colors " +
                      (selected
                        ? "bg-ink text-paper"
                        : "text-ink/60 hover:text-ink")
                    }
                  >
                    {mode}
                  </button>
                );
              })}
            </div>
          </div>
          {reportView === "rendered" ? (
            <article className="prose prose-sm max-w-none bg-white rounded border border-ink/10 p-5">
              <ReactMarkdown>{report}</ReactMarkdown>
            </article>
          ) : (
            <pre className="whitespace-pre-wrap break-words bg-white rounded border border-ink/10 p-5 font-mono text-xs leading-relaxed text-ink/80">
              {report}
            </pre>
          )}
        </section>
      )}

      {critique && (
        <section>
          <h2 className="text-xs font-mono text-ink/50 mb-2">
            critique — <span className="text-ink">{critique.overall}</span>
          </h2>
          {critique.issues.length === 0 ? (
            <p className="text-sm text-ink/60">No issues flagged.</p>
          ) : (
            <ul className="space-y-2">
              {critique.issues.map((issue, i) => (
                <li key={i} className="rounded border border-ink/10 bg-white p-3 text-sm">
                  <span className="font-mono text-xs text-ink/50">[{issue.severity}]</span>{" "}
                  <span className="font-medium">{issue.claim}</span>
                  <p className="mt-1 text-ink/70">{issue.note}</p>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </div>
  );
}
