"use client";

import { useEffect, useRef, useState } from "react";
import { Check, Copy } from "lucide-react";
import { addEntry, subscribe, type LogEntry, type DebugStep } from "@/lib/debug-store";

// ── helpers ───────────────────────────────────────────────────────────────────

function statusColor(status?: number, ok?: boolean, networkError?: string) {
  if (networkError) return "text-destructive";
  if (!status) return "text-muted-foreground";
  if (ok) return "text-green-600 dark:text-green-400";
  if (status >= 500) return "text-destructive";
  return "text-amber-600 dark:text-amber-400";
}

function safeJson(val: unknown): string {
  try {
    return JSON.stringify(val, null, 2);
  } catch {
    return String(val);
  }
}

// ── CopyBtn ───────────────────────────────────────────────────────────────────

function CopyBtn({
  value,
  onCopy,
}: {
  value: unknown;
  onCopy?: (e: React.MouseEvent) => void;
}) {
  const [copied, setCopied] = useState(false);

  function handle(e: React.MouseEvent) {
    e.stopPropagation();
    onCopy?.(e);
    const text = typeof value === "string" ? value : safeJson(value);
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    });
  }

  return (
    <button
      onClick={handle}
      className="ml-1 shrink-0 rounded p-0.5 text-muted-foreground/50 hover:bg-accent hover:text-foreground"
    >
      {copied ? <Check className="size-3" /> : <Copy className="size-3" />}
    </button>
  );
}

// ── StepRow ───────────────────────────────────────────────────────────────────

function StepRow({ step }: { step: DebugStep }) {
  const [open, setOpen] = useState(false);
  const { fn, error, ...rest } = step;
  const isError = !!error;

  return (
    <div className="ml-2 border-l border-border/50 pl-2">
      {/* header */}
      <div
        className="flex cursor-pointer items-center py-0.5"
        onClick={() => setOpen((v) => !v)}
      >
        <span
          className={`select-none font-mono text-[10px] ${
            isError ? "text-destructive" : "text-foreground"
          }`}
        >
          {open ? "▾" : "▸"} {fn}
          {isError && <span className="ml-2 text-destructive">✕</span>}
        </span>
        <CopyBtn value={step} />
      </div>

      {/* expanded content */}
      {open && (
        <pre className="mb-1 whitespace-pre-wrap break-all text-[10px] text-muted-foreground">
          {safeJson({ error, ...rest })}
        </pre>
      )}
    </div>
  );
}

// ── Section ───────────────────────────────────────────────────────────────────
// Generic collapsible section with a copy button in the header.

function Section({
  label,
  copyValue,
  labelColor = "text-gray-500",
  defaultOpen = false,
  children,
}: {
  label: string;
  copyValue: unknown;
  labelColor?: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div>
      <div
        className="flex cursor-pointer items-center hover:opacity-80"
        onClick={() => setOpen((v) => !v)}
      >
        <span className={`select-none text-[10px] ${labelColor}`}>
          {open ? "▾" : "▸"} {label}
        </span>
        <CopyBtn value={copyValue} />
      </div>
      {open && <div className="mt-1">{children}</div>}
    </div>
  );
}

// ── EntryRow ──────────────────────────────────────────────────────────────────

function EntryRow({ entry }: { entry: LogEntry }) {
  const [open, setOpen] = useState(false);
  const {
    method,
    url,
    status,
    ok,
    durationMs,
    requestBody,
    responseBody,
    steps,
    networkError,
  } = entry;
  const shortUrl = url.replace(/^https?:\/\/[^/]+/, "");
  const color = statusColor(status, ok, networkError);

  return (
    <div className="border-b border-border">
      {/* header row */}
      <div
        className="flex cursor-pointer items-center gap-1 px-2 py-1.5 hover:bg-muted"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="shrink-0 font-mono text-[10px] text-muted-foreground">{method}</span>
        <span className="min-w-0 flex-1 truncate font-mono text-[10px] text-foreground">
          {shortUrl}
        </span>
        {status && (
          <span className={`shrink-0 font-mono text-[10px] ${color}`}>{status}</span>
        )}
        {networkError && (
          <span className="shrink-0 text-[10px] text-destructive">ERR</span>
        )}
        <span className="shrink-0 text-[10px] text-muted-foreground/50">{durationMs}ms</span>
        <CopyBtn value={entry} />
      </div>

      {/* expanded */}
      {open && (
        <div className="space-y-2 px-2 pb-2 pt-1">
          {requestBody !== undefined && (
            <Section label="Request body" copyValue={requestBody} labelColor="text-muted-foreground">
              <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all text-[10px] text-muted-foreground">
                {safeJson(requestBody)}
              </pre>
            </Section>
          )}

          {steps && steps.length > 0 && (
            <Section
              label={`Backend steps (${steps.length})`}
              copyValue={steps}
              labelColor="text-primary"
              defaultOpen
            >
              <div className="space-y-0.5">
                {steps.map((s, i) => (
                  <StepRow key={i} step={s} />
                ))}
              </div>
            </Section>
          )}

          {responseBody !== undefined && (
            <Section
              label={ok === false ? "Response (error)" : "Response body"}
              copyValue={responseBody}
              labelColor={ok === false ? "text-destructive" : "text-muted-foreground"}
            >
              <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all text-[10px] text-muted-foreground">
                {safeJson(responseBody)}
              </pre>
            </Section>
          )}

          {networkError && (
            <p className="text-[10px] text-destructive">{networkError}</p>
          )}
        </div>
      )}
    </div>
  );
}

// ── DebugPanel ────────────────────────────────────────────────────────────────

export function DebugPanel() {
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [open, setOpen] = useState(false);
  const patchedRef = useRef(false);

  useEffect(() => {
    document.documentElement.style.setProperty(
      "--debug-panel-w",
      open ? "420px" : "0px"
    );
  }, [open]);

  // Monkey-patch fetch once
  useEffect(() => {
    if (patchedRef.current) return;
    patchedRef.current = true;

    const original = window.fetch;
    window.fetch = async (input, init) => {
      const ts = Date.now();
      const url =
        typeof input === "string"
          ? input
          : input instanceof URL
          ? input.toString()
          : (input as Request).url;
      const method = (
        init?.method ??
        (input instanceof Request ? input.method : "GET")
      ).toUpperCase();

      let requestBody: unknown = undefined;
      if (init?.body && typeof init.body === "string") {
        try {
          requestBody = JSON.parse(init.body);
        } catch {
          requestBody = init.body;
        }
      } else if (init?.body) {
        requestBody = "[binary]";
      }

      const start = Date.now();
      try {
        const response = await original(input, init);
        const durationMs = Date.now() - start;

        let responseBody: unknown = undefined;
        const ct = response.headers.get("content-type") ?? "";
        if (ct.includes("json")) {
          try {
            responseBody = await response.clone().json();
          } catch {}
        }

        let steps: DebugStep[] | undefined;
        const hdr = response.headers.get("x-debug-log");
        if (hdr) {
          try {
            steps = JSON.parse(hdr);
          } catch {}
        }

        addEntry({
          id: ts + url,
          ts,
          method,
          url,
          status: response.status,
          ok: response.ok,
          durationMs,
          requestBody,
          responseBody,
          steps,
        });
        return response;
      } catch (err) {
        addEntry({
          id: ts + url,
          ts,
          method,
          url,
          durationMs: Date.now() - start,
          networkError: String(err),
        });
        throw err;
      }
    };

    return () => {
      window.fetch = original;
      patchedRef.current = false;
    };
  }, []);

  useEffect(() => subscribe(setEntries), []);

  return (
    <>
      <button
        onClick={() => setOpen((v) => !v)}
        className="fixed bottom-4 right-4 z-50 rounded-full bg-background px-3 py-1.5 font-mono text-xs text-foreground shadow-lg ring-1 ring-border hover:bg-muted"
      >
        {open ? "✕ log" : `⬡ log${entries.length ? ` (${entries.length})` : ""}`}
      </button>

      {open && (
        <div className="fixed right-0 top-0 z-40 flex h-screen w-[420px] flex-col bg-background shadow-2xl ring-1 ring-border">
          <div className="flex items-center justify-between border-b border-border px-3 py-2">
            <span className="font-mono text-xs text-muted-foreground">
              Debug log — {entries.length} request{entries.length !== 1 ? "s" : ""}
            </span>
            <div className="flex items-center gap-2">
              <CopyBtn value={entries} />
              <button
                onClick={() => setEntries([])}
                className="text-[10px] text-muted-foreground/50 hover:text-muted-foreground"
              >
                clear
              </button>
            </div>
          </div>
          <div className="flex-1 overflow-y-auto">
            {entries.length === 0 ? (
              <p className="p-4 text-center text-[10px] text-muted-foreground/50">
                No requests yet — click something.
              </p>
            ) : (
              entries.map((e) => <EntryRow key={e.id} entry={e} />)
            )}
          </div>
        </div>
      )}
    </>
  );
}
