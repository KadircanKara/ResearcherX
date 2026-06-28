/** Request-level debug log store. Module-level so fetch interceptor can write to it. */

export type DebugStep = { fn: string; [k: string]: unknown };

export type LogEntry = {
  id: string;
  ts: number;
  method: string;
  url: string;
  status?: number;
  ok?: boolean;
  durationMs: number;
  requestBody?: unknown;
  responseBody?: unknown;
  steps?: DebugStep[];
  networkError?: string;
};

const MAX = 50;
const entries: LogEntry[] = [];
const listeners = new Set<(entries: LogEntry[]) => void>();

export function addEntry(entry: LogEntry) {
  entries.unshift(entry);
  if (entries.length > MAX) entries.pop();
  const snapshot = [...entries];
  listeners.forEach((fn) => fn(snapshot));
}

export function subscribe(fn: (entries: LogEntry[]) => void) {
  listeners.add(fn);
  fn([...entries]);
  return () => listeners.delete(fn);
}
