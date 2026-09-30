"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { routes } from "@/lib/routes";
import { authEnabled, supabase } from "@/lib/supabase";

/**
 * Keeps signed-out visitors out of /admin under real auth. A no-op in dev.
 * Renders nothing until the session is known, so no page flashes and then
 * vanishes.
 */
export function AuthGate({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [ready, setReady] = useState(!authEnabled);

  useEffect(() => {
    if (!authEnabled) return;
    const auth = supabase().auth;
    auth.getSession().then(({ data }) => {
      if (data.session) setReady(true);
      else router.replace(routes.login());
    });
    const { data } = auth.onAuthStateChange((event) => {
      if (event === "SIGNED_OUT") router.replace(routes.login());
    });
    return () => data.subscription.unsubscribe();
  }, [router]);

  return ready ? <>{children}</> : null;
}
