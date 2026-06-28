"use client";

import { useEffect, useRef, useState } from "react";
import { addEntry, subscribe, type LogEntry, type DebugStep } from "@/lib/debug-store";

function statusColor(status?: number, ok?: boolean, networkError?: string) {
  if (networkError) return "text-red-400";
  if (!status) return "text-gray-400";
  if (ok) return "text-green-400";
  if (status >= 500) return "text-red-400";
  return "text-amber-400";
}

function safeJson(val: unknown): string {
  try {
    return JSON.stringify(val, null, 2);
  } catch {
    return String(val);
  }
}

function StepRow({ step }: { step: DebugStep }) {
  const { fn, error, ...rest } = step;
  const isError = !!error;
  return (
    <details className="ml-2 border-l border-gray-700 pl-2">
      <summary
        className={`cursor-pointer select-none py-0.5 font-mono text-[10px] ${
          isError ? "text-red-400" : "text-gray-300"
        }`}
      >
        {fn}
        {isError && <span className="ml-2 text-red-400">✕ {String(error)}</span>}
      </summary>
      <pre className="mt-1 whitespace-pre-wrap break-all text-[10px] text-gray-400">
        {safeJson(rest)}
      </pre>
    </details>
  );
}

function EntryRow({ entry }: { entry: LogEntry }) {
  const { method, url, status, ok, durationMs, requestBody, responseBody, steps, networkError } =
    entry;
  const shortUrl = url.replace(/^https?:\/\/[^/]+/, "");
  const color = statusColor(status, ok, networkError);

  return (
    <details className="border-b border-gray-800">
      <summary className="cursor-pointer select-none px-2 py-1.5 hover:bg-gray-800">
        <span className="font-mono text-[10px] text-gray-500">{method} </span>
        <span className="font-mono text-[10px] text-gray-300">{shortUrl}</span>
        {status && (
          <span className={`ml-2 font-mono text-[10px] ${color}`}>{status}</span>
        )}
        {networkError && <span className="ml-2 text-[10px] text-red-400">ERR</span>}
        <span className="ml-2 text-[10px] text-gray-600">{durationMs}ms</span>
      </summary>

      <div className="space-y-2 px-2 pb-2 pt-1">
        {/* Request body */}
        {requestBody !== undefined && (
          <details>
            <summary className="cursor-pointer select-none text-[10px] text-gray-500">
              Request body
            </summary>
            <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-all text-[10px] text-gray-400">
              {safeJson(requestBody)}
            </pre>
          </details>
        )}

        {/* Backend debug steps */}
        {steps && steps.length > 0 && (
          <details open>
            <summary className="cursor-pointer select-none text-[10px] text-indigo-400">
              Backend steps ({steps.length})
            </summary>
            <div className="mt-1 space-y-0.5">
              {steps.map((s, i) => (
                <StepRow key={i} step={s} />
              ))}
            </div>
          </details>
        )}

        {/* Response body */}
        {responseBody !== undefined && (
          <details>
            <summary
              className={`cursor-pointer select-none text-[10px] ${
                ok === false ? "text-red-400" : "text-gray-500"
              }`}
            >
              Response {ok === false ? "(error)" : "body"}
            </summary>
            <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-all text-[10px] text-gray-400">
              {safeJson(responseBody)}
            </pre>
          </details>
        )}

        {networkError && (
          <p className="text-[10px] text-red-400">{networkError}</p>
        )}
      </div>
    </details>
  );
}

export function DebugPanel() {
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [open, setOpen] = useState(false);
  const patchedRef = useRef(false);

  // Monkey-patch fetch once to capture all requests
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

  // Subscribe to store updates
  useEffect(() => subscribe(setEntries), []);

  return (
    <>
      {/* Toggle button */}
      <button
        onClick={() => setOpen((v) => !v)}
        className="fixed bottom-4 right-4 z-50 rounded-full bg-gray-900 px-3 py-1.5 font-mono text-xs text-gray-300 shadow-lg ring-1 ring-gray-700 hover:bg-gray-800"
      >
        {open ? "✕ log" : `⬡ log${entries.length ? ` (${entries.length})` : ""}`}
      </button>

      {/* Panel */}
      {open && (
        <div className="fixed right-0 top-0 z-40 flex h-screen w-[420px] flex-col bg-gray-950 shadow-2xl ring-1 ring-gray-800">
          <div className="flex items-center justify-between border-b border-gray-800 px-3 py-2">
            <span className="font-mono text-xs text-gray-400">
              Debug log — {entries.length} request{entries.length !== 1 ? "s" : ""}
            </span>
            <button
              onClick={() => {
                entries.length = 0; // clear visible state
                setEntries([]);
              }}
              className="text-[10px] text-gray-600 hover:text-gray-400"
            >
              clear
            </button>
          </div>
          <div className="flex-1 overflow-y-auto">
            {entries.length === 0 ? (
              <p className="p-4 text-center text-[10px] text-gray-600">
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
