"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import type { EmailOtpType } from "@supabase/supabase-js";
import { AuthShell } from "@/components/auth/auth-shell";
import { authErrorMessage, parseEmailLink } from "@/lib/auth-flow";
import { routes } from "@/lib/routes";
import { authEnabled, supabase } from "@/lib/supabase";

/**
 * The activation link lands here (`deploy/supabase/confirm-email.html`):
 * `?token_hash=…&type=email`. Verifying it activates the account and signs
 * the user in.
 *
 * The token is verified by THIS page's script rather than by a GET to
 * Supabase's own verify URL, for two reasons: it works in any browser (the
 * default PKCE redirect needs the browser that signed up), and mail scanners
 * that pre-open links (Outlook Safe Links and the like) fetch the page
 * without running it, so they cannot spend the one-time token.
 */
export default function ConfirmPage() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  // StrictMode runs effects twice in dev; a second verify of a one-time
  // token always fails and would overwrite the success.
  const started = useRef(false);

  useEffect(() => {
    if (!authEnabled || started.current) return;
    started.current = true;
    const link = parseEmailLink(window.location.search, "confirm");
    if (!link) return setError("This activation link is incomplete. Open it from the email again.");
    supabase()
      .auth.verifyOtp({ token_hash: link.tokenHash, type: link.type as EmailOtpType })
      .then(({ error: err }) => {
        if (err) return setError(authErrorMessage(err));
        router.replace(routes.research());
      });
  }, [router]);

  return (
    <AuthShell title={error ? "Activation failed" : "Activating your account…"} error={error}>
      {error && (
        <p className="text-sm">
          <Link className="underline" href={routes.login()}>
            Sign in
          </Link>{" "}
          to request a new activation email, or{" "}
          <Link className="underline" href={routes.signup()}>
            sign up again
          </Link>
          .
        </p>
      )}
    </AuthShell>
  );
}
