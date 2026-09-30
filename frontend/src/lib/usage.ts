import { ApiError } from "./api-error";

export interface Usage {
  papers: { used: number; limit: number };
  chat_turns_today: { used: number; limit: number; resets_at: string };
}

/** "12 / 20 papers", or null when papers are unlimited. */
export function papersLine(u: Usage): string | null {
  return u.papers.limit > 0 ? `${u.papers.used} / ${u.papers.limit} papers` : null;
}

/** Shown only when the day is running low: fewer than 20 turns left. */
export function turnsLeftLine(u: Usage): string | null {
  const { used, limit } = u.chat_turns_today;
  if (limit <= 0) return null;
  const left = Math.max(0, limit - used);
  if (left >= 20) return null;
  return `${left} ${left === 1 ? "turn" : "turns"} left today`;
}

/** A limit refusal's text, verbatim from the server; null for other errors. */
export function limitMessage(err: unknown): string | null {
  return err instanceof ApiError && err.status === 429 ? err.detail : null;
}
