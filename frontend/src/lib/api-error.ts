/** A non-2xx backend answer, carrying the server's fixed `detail` text. */
export class ApiError extends Error {
  constructor(public status: number, public detail: string | null, label = "request") {
    super(`${label} -> ${status}`);
  }
}

/** Parse `{"detail": "..."}` off a failed response; null when absent. */
export async function detailOf(r: Response): Promise<string | null> {
  const body = await r.json().catch(() => null);
  return typeof body?.detail === "string" ? body.detail : null;
}
