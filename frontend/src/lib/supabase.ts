import { createClient, type SupabaseClient } from "@supabase/supabase-js";

/**
 * The browser's Supabase Auth client. Both values are PUBLIC by design (the
 * anon key only lets a browser ask Supabase for a sign-in code) and are
 * inlined at build time. Absent in dev, where the app stays on the
 * `X-Dev-User-Id` seam and nothing here runs.
 */
const url = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "";

export const authEnabled = url !== "" && anonKey !== "";

let client: SupabaseClient | null = null;

export function supabase(): SupabaseClient {
  if (!authEnabled) throw new Error("Supabase auth is not configured");
  client ??= createClient(url, anonKey);
  return client;
}

/** The current access token, refreshed by supabase-js as needed. */
export async function accessToken(): Promise<string | null> {
  if (!authEnabled) return null;
  const { data } = await supabase().auth.getSession();
  return data.session?.access_token ?? null;
}
