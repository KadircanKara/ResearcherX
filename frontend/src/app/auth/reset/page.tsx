"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AuthShell, FieldRow } from "@/components/auth/auth-shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { PASSWORD_MIN, authErrorMessage, parseEmailLink, passwordProblem } from "@/lib/auth-flow";
import { routes } from "@/lib/routes";
import { authEnabled, supabase } from "@/lib/supabase";

/**
 * The reset link lands here (`deploy/supabase/reset-email.html`):
 * `?token_hash=…&type=recovery`. Verifying it opens a recovery session; the
 * new password is then set on that session. Verified by script for the same
 * reasons as /auth/confirm.
 */
export default function ResetPasswordPage() {
  const router = useRouter();
  const [phase, setPhase] = useState<"verifying" | "ready" | "failed">("verifying");
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);

  useEffect(() => {
    if (!authEnabled || started.current) return;
    started.current = true;
    const link = parseEmailLink(window.location.search, "recovery");
    if (!link) {
      setPhase("failed");
      return setError("This reset link is incomplete. Open it from the email again.");
    }
    supabase()
      .auth.verifyOtp({ token_hash: link.tokenHash, type: "recovery" })
      .then(({ error: err }) => {
        if (err) {
          setPhase("failed");
          return setError(authErrorMessage(err));
        }
        // The token is spent; keep it out of history and the Referer header.
        window.history.replaceState(null, "", routes.authReset());
        setPhase("ready");
      });
  }, []);

  async function save() {
    const { data } = await supabase().auth.getUser();
    const problem = passwordProblem(password, data.user?.email ?? "");
    if (problem) return setError(problem);
    if (password !== repeat) return setError("The two passwords don't match.");
    setBusy(true);
    setError(null);
    const { error: err } = await supabase().auth.updateUser({ password });
    setBusy(false);
    if (err) return setError(authErrorMessage(err));
    router.replace(routes.research());
  }

  if (phase !== "ready") {
    return (
      <AuthShell title={phase === "failed" ? "Reset failed" : "Checking your link…"} error={error}>
        {phase === "failed" && (
          <Link className="text-sm underline" href={routes.forgotPassword()}>
            Request a new reset link
          </Link>
        )}
      </AuthShell>
    );
  }

  return (
    <AuthShell title="Choose a new password" error={error}>
      <form
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <FieldRow id="password" label="New password">
          <Input
            id="password"
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            aria-describedby="password-hint"
          />
        </FieldRow>
        <p id="password-hint" className="text-xs text-muted-foreground">
          At least {PASSWORD_MIN} characters.
        </p>
        <FieldRow id="repeat" label="Repeat new password">
          <Input
            id="repeat"
            type="password"
            autoComplete="new-password"
            value={repeat}
            onChange={(e) => setRepeat(e.target.value)}
          />
        </FieldRow>
        <Button type="submit" className="w-full" disabled={busy}>
          Save password and sign in
        </Button>
      </form>
    </AuthShell>
  );
}
