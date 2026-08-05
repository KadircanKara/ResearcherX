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

// Monotonic counter, not `ts + url` — concurrent fetches to the same URL land
// in the same millisecond and would collide as React keys.
let seq = 0;

export function addEntry(entry: Omit<LogEntry, "id">) {
  entries.unshift({ ...entry, id: `${entry.ts}-${++seq}` });
  if (entries.length > MAX) entries.pop();
  const snapshot = [...entries];
  listeners.forEach((fn) => fn(snapshot));
}

export function clearEntries() {
  entries.length = 0;
  listeners.forEach((fn) => fn([]));
}

export function subscribe(fn: (entries: LogEntry[]) => void) {
  listeners.add(fn);
  fn([...entries]);
  // Braces matter: Set.delete returns boolean, which is not a valid
  // useEffect destructor return type.
  return () => {
    listeners.delete(fn);
  };
}
